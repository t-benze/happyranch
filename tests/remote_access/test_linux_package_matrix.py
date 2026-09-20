"""TASK8446 installer filesystem causal matrix (seq246 A/B/C; TASK8468 finding 4).

This module is the *complete finite enumeration* of the real installer
filesystem operations accepted in
``engineering_manager/output/TASK-8446/seq246-corrected-cases.md`` sections
A/B/C: preparation (P0-P3), staged payload/manifest (P1-P2), drop-in backup
(P4), every ownership/progress/rollback/commit record update (P5-rN), the
rename/publication boundaries (I1-I5) and committed cleanup interiors
(C1-C3), with separate exception-rollback (RB) and interruption-recovery
(REC) routes (R1-R5).

The enumeration is derived from the *real* execution: a recording guard
captures the ordered ``(stage, operation, path)`` seams of an actual
fault-free install (and of an actual recovery), and every captured call is
then faulted in turn.  No operation is invented and no whole family is mapped
to a representative.  ``pytest_generate_tests`` turns the finite sorted list
into one maintained test parameter per real call and fault mode, each with an
exact-one-hit receipt, an immediate-state oracle and two successful real
reinstalls.

Fault modes per accepted section B: ``E`` = ``OSError`` raised before/after
the saved binding, ``P`` = a real partial destination write/copy followed by
``OSError``, and ``K`` = a one-shot ``BaseException`` escaping ordinary
cleanup.  A ``K`` (or partial residue) before the first durable ownership
record is unrecorded preparation residue: it is preserved and refused (M9),
never mistaken for a coherent owned transaction.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from pathlib import PurePosixPath

import pytest

from runtime.remote_access.linux_package import (
    PackageError,
    TRANSACTION_MARKER,
    UNITS,
    _PAYLOAD_BACKUP_NAME,
    _UNIT_BACKUP_NAME,
    _inventory_tree,
    _recover_interrupted,
    _record_temp,
    _tree_matches,
    build_linux_package,
    install_linux_package,
)
from tests.remote_access.test_linux_package import (
    _rewrite_package,
    _stage_system_credentials,
)

FAILURE = "injected"


class _Interrupted(BaseException):
    """A one-shot process interruption that escapes ordinary rollback."""


# ---------------------------------------------------------------------------
# Fixtures: small but genuinely loss-detecting OLD/NEW packages
# ---------------------------------------------------------------------------

def _tiny_inputs(folder: Path, label: str):
    sidecar = folder / f"sidecar-{label}"
    sidecar.write_bytes(b"sidecar-" + label.encode())
    connector = folder / f"connector-{label}"
    connector.write_bytes(b"connector-" + label.encode())
    wheel = folder / f"happyranch-{label}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as built:
        built.writestr("runtime/__init__.py", "")
        built.writestr("runtime/remote_access/__init__.py", "")
        built.writestr("runtime/remote_access/cli.py", f"VERSION = {label!r}\n")
    license_text = "fixture license\n"
    digest = hashlib.sha256(license_text.encode()).hexdigest()
    inventory = folder / f"inventory-{label}.json"
    inventory.write_text(json.dumps({
        "schema_version": 1,
        "artifact": {"goos": "linux", "goarch": "amd64", "cgo_enabled": False,
                     "package": "happyranch/linux-tsnet-sidecar"},
        "generator": "tools/generate_inventory.py",
        "modules": [{"module": "example.test/mod", "version": "v1", "sum": "h1:x",
                     "source": "https://example.test/mod", "spdx": "MIT",
                     "license_sha256": digest,
                     "relationship": "statically-linked-linux-build-input"}],
    }) + "\n")
    notices = folder / f"notices-{label}.md"
    notices.write_text(
        "# notices\n\n---\nModules:\n- example.test/mod@v1\n\nSPDX: MIT\n"
        "License-SHA256: " + digest + "\n\n```text\n" + license_text.rstrip() + "\n```\n"
    )
    return sidecar, connector, wheel, inventory, notices


def _tiny_package(folder: Path, version: str, label: str) -> Path:
    """A real package whose sidecar, connector, wheel AND unit bodies differ."""
    built = build_linux_package(
        folder / f"pkg-{label}.tar", *_tiny_inputs(folder, label), version=version
    )

    def mutate(entries) -> None:
        manifest_index = next(
            index for index, (member, _raw) in enumerate(entries)
            if member.name.endswith("manifest.json")
        )
        manifest_member, manifest_raw = entries[manifest_index]
        manifest = json.loads(manifest_raw)
        for index, (member, raw) in enumerate(entries):
            if "/systemd/" not in member.name:
                continue
            mutated = raw + f"# {label}\n".encode()
            entries[index] = (member, mutated)
            relative = str(PurePosixPath(member.name).relative_to("happyranch-linux-amd64"))
            next(item for item in manifest["files"] if item["path"] == relative)[
                "sha256"
            ] = hashlib.sha256(mutated).hexdigest()
        entries[manifest_index] = (manifest_member, json.dumps(manifest).encode())

    return _rewrite_package(built, folder / f"pkg-{label}-units.tar", mutate)


_DISCOVERY_ROOT = Path(tempfile.mkdtemp(prefix="task8480-matrix-"))
atexit.register(lambda: shutil.rmtree(_DISCOVERY_ROOT, ignore_errors=True))
_PACKAGES: tuple[Path, Path] | None = None


def _packages() -> tuple[Path, Path]:
    global _PACKAGES
    if _PACKAGES is None:
        folder = _DISCOVERY_ROOT / "packages"
        folder.mkdir(parents=True, exist_ok=True)
        _PACKAGES = (
            _tiny_package(folder, "1-OLD", "OLD"),
            _tiny_package(folder, "2-NEW", "NEW"),
        )
    return _PACKAGES


def _node(path: Path) -> tuple:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        kind: tuple = ("link", os.readlink(path))
    elif stat.S_ISDIR(metadata.st_mode):
        kind = ("dir", stat.S_IMODE(metadata.st_mode))
    elif stat.S_ISREG(metadata.st_mode):
        kind = ("file", path.read_bytes(), stat.S_IMODE(metadata.st_mode))
    else:
        kind = ("other", stat.S_IFMT(metadata.st_mode))
    return (kind, metadata.st_uid, metadata.st_gid)


def _snapshot(root: Path) -> dict[str, tuple]:
    state = {".": _node(root)}
    for path in sorted(root.rglob("*")):
        state[str(path.relative_to(root))] = _node(path)
    return state


class _Config:
    def __init__(self, name: str, *, upgrade: bool, system_service: bool,
                 enrollment: bool) -> None:
        self.name = name
        self.upgrade = upgrade
        self.system_service = system_service
        self.enrollment = enrollment


_CONFIGS = (
    _Config("upgrade-noroot", upgrade=True, system_service=False, enrollment=False),
    _Config("fresh-noroot", upgrade=False, system_service=False, enrollment=False),
    _Config("upgrade-system", upgrade=True, system_service=True, enrollment=True),
    _Config("upgrade-system-nodropin", upgrade=True, system_service=True, enrollment=False),
    _Config("fresh-system", upgrade=False, system_service=True, enrollment=True),
)


def _build_base(root: Path, config: _Config, old: Path) -> None:
    if config.system_service:
        _stage_system_credentials(root, enrollment=config.enrollment)
    if config.upgrade:
        install_linux_package(old, root, system_service=config.system_service)
    else:
        root.mkdir(parents=True, exist_ok=True)
        (root / "unrelated.txt").write_bytes(b"keep")


def _apply_loss_detecting_prior(root: Path, config: _Config) -> None:
    """Give the upgrade baseline three distinct unit modes and a distinct drop-in."""
    if not config.upgrade:
        return
    for index, unit in enumerate(UNITS):
        target = root / "etc/systemd/system" / unit
        target.write_bytes(f"OLD-unit-{index}-{unit}".encode())
        target.chmod((0o600, 0o640, 0o644)[index])
    if config.system_service and config.enrollment:
        dropin_dir = root / "etc/systemd/system/happyranch-tsnet-sidecar.service.d"
        dropin_dir.mkdir(parents=True, exist_ok=True)
        dropin_dir.chmod(0o710)
        dropin = dropin_dir / "10-enrollment-credential.conf"
        dropin.write_bytes(b"operator-managed-prior-dropin\n")
        dropin.chmod(0o640)
        sibling = dropin_dir / "99-foreign-sibling.conf"
        sibling.write_bytes(b"foreign-sibling\n")
        sibling.chmod(0o600)


# ---------------------------------------------------------------------------
# Real binding fault injectors
# ---------------------------------------------------------------------------

class _Rule:
    def __init__(self, *, operation: str, stage: str, occurrence: int,
                 exception: type[BaseException], partial: bool = False) -> None:
        self.operation = operation
        self.stage = stage
        self.occurrence = occurrence
        self.exception = exception
        self.partial = partial
        self.seen = 0
        self.raises = 0


class _SeamGuard:
    """Fault exactly one saved binding call and record the real trace.

    ``trigger`` is an optional publication-phase rule that must fire before the
    ordinary ``rules`` become active; this keeps an exception-rollback (RB)
    recovery rule from matching the identically named publication record seams
    during the first half of the same install.
    """

    def __init__(self, rules: list[_Rule] | None = None, *, armed: bool = True,
                 trigger: _Rule | None = None) -> None:
        self.rules = rules or []
        self.armed = armed
        self.trigger = trigger
        self.trace: list[tuple[str, str, str]] = []

    def _fire(self, rule: _Rule, path: str) -> None:
        if rule.partial:
            Path(path).write_bytes(b"partial-new-bytes")
        rule.raises += 1
        raise rule.exception(FAILURE)

    def _match(self, rule: _Rule, stage: str, operation: str, path: str) -> bool:
        if stage != rule.stage or operation != rule.operation:
            return False
        rule.seen += 1
        if rule.seen != rule.occurrence:
            return False
        self._fire(rule, path)
        return True

    def __call__(self, stage: str, operation: str, path: str) -> None:
        self.trace.append((stage, operation, path))
        if not self.armed:
            rule = self.trigger
            if rule is not None and stage == rule.stage and operation == rule.operation:
                rule.seen += 1
                if rule.seen == rule.occurrence:
                    self.armed = True
                    self._fire(rule, path)
            return
        for rule in self.rules:
            self._match(rule, stage, operation, path)


def _publication_rule(config: _Config, exception: type[BaseException]) -> _Rule:
    """The real publication binding whose interruption leaves the widest state.

    The chosen binding is disjoint from every rollback/recovery operation name
    so a single armed-after-trigger guard can inject both halves of route RB.
    """
    if config.system_service and config.enrollment and config.upgrade:
        return _Rule(operation="dropin_publish", stage="after", occurrence=1,
                     exception=exception)
    return _Rule(operation=f"unit_publish:{UNITS[-1]}", stage="after", occurrence=1,
                 exception=exception)


_WRITE_PREFIXES = ("stage_payload", "stage_manifest", "unit_publish", "dropin_publish",
                   "unit_backup", "dropin_backup")


def _is_write(operation: str) -> bool:
    return (
        any(operation == prefix or operation.startswith(prefix + ":")
            for prefix in _WRITE_PREFIXES)
        and not operation.endswith(":chmod")
        and not operation.endswith(":mkdir")
    )


def _ranks(trace: list[tuple[str, str, str]]) -> dict[int, int]:
    seen: Counter = Counter()
    ranks: dict[int, int] = {}
    for index, (stage, operation, _path) in enumerate(trace):
        seen[(stage, operation)] += 1
        ranks[index] = seen[(stage, operation)]
    return ranks


_WORK = _DISCOVERY_ROOT / "discovery"
_CACHE: dict[str, dict] = {}


def _config_cache(config: _Config) -> dict:
    if config.name in _CACHE:
        return _CACHE[config.name]
    old, new = _packages()
    base = _WORK / config.name
    template = base / "template"
    root = base / "trace"
    try:
        _build_base(template, config, old)
        old_snapshot = _snapshot(template)
        old_payload = (
            _inventory_tree(template / "opt/happyranch")
            if (template / "opt/happyranch").is_dir() else None
        )
        shutil.copytree(template, root, symlinks=True)
        guard = _SeamGuard()
        install_linux_package(new, root, system_service=config.system_service, guard=guard)
        trace = guard.trace
        commit = max(
            index for index, (stage, op, _p) in enumerate(trace)
            if op == "record_replace" and stage == "after"
        )
        first_record = next(
            index for index, (stage, op, _p) in enumerate(trace)
            if op == "record_replace" and stage == "after"
        )
        expected = base / "expected"
        shutil.copytree(template, expected, symlinks=True)
        install_linux_package(new, expected, system_service=config.system_service)
        new_snapshot = _snapshot(expected)
    except BaseException:
        raise
    _CACHE[config.name] = {
        "config": config,
        "template": template,
        "old": old_snapshot,
        "new": new_snapshot,
        "old_payload": old_payload,
        "trace": trace,
        "ranks": _ranks(trace),
        "commit": commit,
        "first_record": first_record,
    }
    return _CACHE[config.name]


class _PubCase:
    def __init__(self, cache: dict, index: int, mode: str) -> None:
        self.cache = cache
        self.index = index
        self.mode = mode
        stage, operation, path = cache["trace"][index]
        self.stage = stage
        self.operation = operation
        self.relpath = Path(path).name
        self.occurrence = cache["ranks"][index]
        self.id = (
            f"{cache['config'].name}-{mode}-{operation}-{stage}-{self.occurrence}"
        )


_MODE_EXCEPTION = {"E": OSError, "P": OSError, "K": _Interrupted}


def _publication_cases() -> list[_PubCase]:
    cases: list[_PubCase] = []
    for config in _CONFIGS:
        cache = _config_cache(config)
        trace = cache["trace"]
        for index, (_stage, operation, _path) in enumerate(trace):
            for mode in ("E", "K", "P"):
                if mode == "P" and not _is_write(operation):
                    continue
                cases.append(_PubCase(cache, index, mode))
    return cases


# ---------------------------------------------------------------------------
# Recovery / rollback finite enumeration (RB and REC)
# ---------------------------------------------------------------------------

_RECOVERY_CONFIGS = tuple(
    config for config in _CONFIGS
    if config.name in {"upgrade-noroot", "upgrade-system", "fresh-system"}
)


def _recovery_cache(config: _Config) -> dict:
    key = "recovery:" + config.name
    if key in _CACHE:
        return _CACHE[key]
    old, new = _packages()
    base = _WORK / ("recovery-" + config.name)
    template = base / "template"
    _build_base(template, config, old)
    _apply_loss_detecting_prior(template, config)
    old_snapshot = _snapshot(template)
    old_payload = (
        _inventory_tree(template / "opt/happyranch")
        if (template / "opt/happyranch").is_dir() else None
    )
    old_units = {}
    if config.upgrade:
        for unit in UNITS:
            path = template / "etc/systemd/system" / unit
            old_units[unit] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": stat.S_IMODE(path.lstat().st_mode),
            }
    dropin = None
    dropin_path = template / "etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf"
    if dropin_path.is_file():
        dropin = {
            "sha256": hashlib.sha256(dropin_path.read_bytes()).hexdigest(),
            "mode": stat.S_IMODE(dropin_path.lstat().st_mode),
        }
    # Build the widest interrupted (uncommitted) state using real production
    # publication, then record the real recovery operation trace.
    interrupted = base / "interrupted"
    shutil.copytree(template, interrupted, symlinks=True)
    trigger = _publication_rule(config, _Interrupted)
    with pytest.raises(_Interrupted):
        install_linux_package(new, interrupted, system_service=config.system_service,
                              guard=_SeamGuard(armed=False, trigger=trigger))
    expected = base / "expected"
    shutil.copytree(template, expected, symlinks=True)
    install_linux_package(new, expected, system_service=config.system_service)
    # The durable record binds its exact root path, so record the real
    # recovery trace on the same in-place interrupted state.
    recording = _SeamGuard()
    _recover_interrupted(interrupted, recording)
    _CACHE[key] = {
        "config": config,
        "template": template,
        "old": old_snapshot,
        "new": _snapshot(expected),
        "old_payload": old_payload,
        "old_units": old_units,
        "dropin": dropin,
        "trace": recording.trace,
        "ranks": _ranks(recording.trace),
    }
    return _CACHE[key]


class _RRCacheUnset(Exception):
    pass


class _RRCase:
    def __init__(self, cache: dict, index: int, route: str, mode: str) -> None:
        self.cache = cache
        self.index = index
        self.route = route
        self.mode = mode
        stage, operation, path = cache["trace"][index]
        self.stage = stage
        self.operation = operation
        self.relpath = Path(path).name
        self.occurrence = cache["ranks"][index]
        self.id = (
            f"{cache['config'].name}-{route}-{mode}-{operation}-{stage}-{self.occurrence}"
        )


def _recovery_cases() -> list[_RRCase]:
    cases: list[_RRCase] = []
    for config in _RECOVERY_CONFIGS:
        cache = _recovery_cache(config)
        for index in range(len(cache["trace"])):
            for route in ("RB", "REC"):
                for mode in ("E", "K"):
                    cases.append(_RRCase(cache, index, route, mode))
    return cases


# ---------------------------------------------------------------------------
# Parametrization
# ---------------------------------------------------------------------------

def pytest_generate_tests(metafunc) -> None:
    if "pub_case" in metafunc.fixturenames:
        cases = _publication_cases()
        metafunc.parametrize("pub_case", cases, ids=[case.id for case in cases])
    if "rr_case" in metafunc.fixturenames:
        cases = _recovery_cases()
        metafunc.parametrize("rr_case", cases, ids=[case.id for case in cases])


# ---------------------------------------------------------------------------
# Oracles
# ---------------------------------------------------------------------------

def _old_artifacts_untouched(state: dict, old: dict) -> bool:
    return all(rel in state and state[rel] == value for rel, value in old.items())


def _payload_is_new(root: Path, new_root: Path) -> bool:
    opt = root / "opt/happyranch"
    expected = new_root / "opt/happyranch"
    return opt.is_dir() and not opt.is_symlink() and _inventory_tree(opt) == _inventory_tree(expected)


def _prior_artifact_retained(root: Path, relative: str, identity: dict,
                             backup_root: Path) -> bool:
    target = root / relative
    if target.is_file() and not target.is_symlink():
        metadata = target.lstat()
        if (stat.S_IMODE(metadata.st_mode) == identity["mode"]
                and hashlib.sha256(target.read_bytes()).hexdigest() == identity["sha256"]):
            return True
    candidate = backup_root / Path(relative).name
    if candidate.is_file() and not candidate.is_symlink():
        metadata = candidate.lstat()
        return (
            stat.S_IMODE(metadata.st_mode) == identity["mode"]
            and hashlib.sha256(candidate.read_bytes()).hexdigest() == identity["sha256"]
        )
    return False


# ---------------------------------------------------------------------------
# A/B/C. Publication-path finite matrix (P0-P5, I1-I4, C1-C3, M9)
# ---------------------------------------------------------------------------

def test_publication_operation_fault_matrix(tmp_path: Path, pub_case: _PubCase) -> None:
    cache = pub_case.cache
    config = cache["config"]
    old, new = _packages()
    case = tmp_path / "root"
    shutil.copytree(cache["template"], case, symlinks=True)

    pre_commit = pub_case.index < cache["commit"]
    unrecorded = (
        pre_commit
        and pub_case.index < cache["first_record"]
        and pub_case.index >= 1
        and pub_case.mode in ("K", "P")
    )
    rule = _Rule(
        operation=pub_case.operation,
        stage=pub_case.stage,
        occurrence=pub_case.occurrence,
        exception=_MODE_EXCEPTION[pub_case.mode],
        partial=pub_case.mode == "P",
    )
    guard = _SeamGuard([rule])
    with pytest.raises((OSError, _Interrupted)):
        install_linux_package(new, case, system_service=config.system_service, guard=guard)
    assert rule.raises == 1

    if unrecorded:
        # M9: no durable ownership record yet; the unknown orphan is preserved
        # and refused, and no OLD artifact changed.
        observed = _snapshot(case)
        assert _old_artifacts_untouched(observed, cache["old"])
        for _ in range(2):
            with pytest.raises(PackageError, match="transaction_state_invalid"):
                _recover_interrupted(case)
        assert _snapshot(case) == observed
        return

    if pub_case.mode == "E" and pre_commit:
        assert _snapshot(case) == cache["old"]
    if pub_case.mode == "E" and not pre_commit:
        assert _payload_is_new(case, _WORK / config.name / "expected")

    _recover_interrupted(case)
    if pre_commit:
        assert _snapshot(case) == cache["old"]
    install_linux_package(new, case, system_service=config.system_service)
    install_linux_package(new, case, system_service=config.system_service)
    assert _snapshot(case) == cache["new"]


# ---------------------------------------------------------------------------
# R1-R5. Separate rollback (RB) and interrupted-recovery (REC) routes
# ---------------------------------------------------------------------------

def _build_interrupted_state(case: Path, config: _Config, new: Path,
                            exception: type[BaseException] = _Interrupted) -> None:
    trigger = _publication_rule(config, exception)
    with pytest.raises(exception):
        install_linux_package(new, case, system_service=config.system_service,
                              guard=_SeamGuard(armed=False, trigger=trigger))


def test_recovery_operation_fault_matrix(tmp_path: Path, rr_case: _RRCase) -> None:
    cache = rr_case.cache
    config = cache["config"]
    _old, new = _packages()
    case = tmp_path / "root"
    shutil.copytree(cache["template"], case, symlinks=True)

    recovery_rule = _Rule(
        operation=rr_case.operation,
        stage=rr_case.stage,
        occurrence=rr_case.occurrence,
        exception=_MODE_EXCEPTION[rr_case.mode],
    )
    if rr_case.route == "REC":
        _build_interrupted_state(case, config, new)
        with pytest.raises((OSError, _Interrupted)):
            _recover_interrupted(case, _SeamGuard([recovery_rule]))
    else:
        # The publication rule triggers the real exception rollback; only then
        # does the recovery rule become active.
        trigger = _publication_rule(config, OSError)
        guard = _SeamGuard([recovery_rule], armed=False, trigger=trigger)
        with pytest.raises((OSError, _Interrupted)):
            install_linux_package(new, case, system_service=config.system_service, guard=guard)
    assert recovery_rule.raises == 1

    # While faulted, every OLD byte/mode remains reachable in its active or
    # recorded backup location.
    if cache["old_payload"] is not None:
        active = case / "opt/happyranch"
        backup = case / _PAYLOAD_BACKUP_NAME
        assert _tree_matches(active, cache["old_payload"]) or _tree_matches(
            backup, cache["old_payload"]
        )
    unit_backup = case / _UNIT_BACKUP_NAME
    for unit, identity in cache["old_units"].items():
        assert _prior_artifact_retained(
            case, f"etc/systemd/system/{unit}", identity, unit_backup
        )
    if cache["dropin"] is not None and config.system_service:
        assert _prior_artifact_retained(
            case,
            "etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf",
            cache["dropin"],
            unit_backup / "happyranch-tsnet-sidecar.service.d",
        )

    _recover_interrupted(case)
    assert _snapshot(case) == cache["old"]
    install_linux_package(new, case, system_service=config.system_service)
    install_linux_package(new, case, system_service=config.system_service)
    assert _snapshot(case) == cache["new"]


# ---------------------------------------------------------------------------
# C. Disjoint ownership negatives (M1-M9): two-call unchanged oracles
# ---------------------------------------------------------------------------

def _rewrite_record(root: Path, mutate) -> dict:
    marker = root / TRANSACTION_MARKER
    record = json.loads(marker.read_text(encoding="utf-8"))
    mutate(record)
    marker.write_text(json.dumps(record), encoding="utf-8")
    marker.chmod(0o600)
    return record


def _preparing_state(tmp_path: Path, config: _Config, name: str) -> tuple[Path, Path]:
    """A real preparing record with an allocated stage and no OLD mutation."""
    old, new = _packages()
    root = tmp_path / name / "root"
    _build_base(root, config, old)
    guard = _SeamGuard([_Rule(operation="stage_payload:bin/happyranch-connector",
                              stage="before", occurrence=1, exception=_Interrupted)])
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, system_service=config.system_service, guard=guard)
    assert json.loads((root / TRANSACTION_MARKER).read_text())["phase"] == "preparing"
    return root, new


def _uncommitted_state(tmp_path: Path, config: _Config, name: str,
                       checkpoint: str = "payload_published") -> tuple[Path, Path]:
    old, new = _packages()
    root = tmp_path / name / "root"
    _build_base(root, config, old)

    def fault(moment: str) -> None:
        if moment == checkpoint:
            raise _Interrupted(FAILURE)

    with pytest.raises(_Interrupted):
        install_linux_package(new, root, system_service=config.system_service, fault=fault)
    assert (root / TRANSACTION_MARKER).exists()
    return root, new


def _refuse_twice(root: Path, before: dict, recovery=True) -> None:
    assert _snapshot(root) == before
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            if recovery:
                _recover_interrupted(root)
            else:
                install_linux_package(_packages()[1], root)
    assert _snapshot(root) == before


_UPGRADE = _CONFIGS[0]
_SYSTEM = _CONFIGS[2]


# M1: current owned publication with explicit prior absence / prior presence.

def test_m1_fresh_owned_publication_recovers_to_absence(tmp_path: Path) -> None:
    root, new = _uncommitted_state(tmp_path, _CONFIGS[1], "m1-fresh")
    _recover_interrupted(root)
    assert not (root / "opt").exists()
    assert not list(root.glob(".happyranch-*"))
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-NEW"


def test_m1_upgrade_uncommitted_recovers_to_old_then_new(tmp_path: Path) -> None:
    root, new = _uncommitted_state(tmp_path, _UPGRADE, "m1-upgrade")
    _recover_interrupted(root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-OLD"
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-NEW"


def test_m1_committed_new_is_preserved_and_reentered(tmp_path: Path) -> None:
    old, new = _packages()
    root = tmp_path / "m1-committed" / "root"
    _build_base(root, _UPGRADE, old)
    trigger = _publication_rule(_UPGRADE, _Interrupted)
    assert trigger.operation == f"unit_publish:{UNITS[-1]}"
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=_SeamGuard(armed=False, trigger=trigger))
    _recover_interrupted(root)
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-NEW"
    assert not list(root.glob(".happyranch-*"))


# M2: legacy schema-v1 compositions preserve and refuse.

@pytest.mark.parametrize("phase", ["prepared", "payload_retained", "payload_published", "units_publishing"])
@pytest.mark.parametrize("residue", ["absent", "payload", "units", "partial"])
def test_m2_legacy_v1_composition_refused_unchanged(tmp_path: Path, phase: str,
                                                    residue: str) -> None:
    old, new = _packages()
    root = tmp_path / f"m2-{phase}-{residue}" / "root"
    _build_base(root, _UPGRADE, old)
    marker = root / TRANSACTION_MARKER
    marker.write_text(json.dumps({"schema_version": 1, "phase": phase}) + "\n")
    marker.chmod(0o600)
    if residue in {"payload", "partial"}:
        backup = root / _PAYLOAD_BACKUP_NAME
        backup.mkdir(mode=0o700)
        (backup / "stray").write_bytes(b"stray")
    if residue in {"units", "partial"}:
        backup = root / _UNIT_BACKUP_NAME
        backup.mkdir(mode=0o700)
        (backup / "stray").write_bytes(b"stray")
    _refuse_twice(root, _snapshot(root))


# M3: malformed / wrong-typed / unknown records.

@pytest.mark.parametrize("mutation", [
    "truncated", "nonobject", "missing-key", "extra-key", "bool-schema",
    "unknown-version", "unknown-phase", "bad-attempt", "unit-not-string",
    "duplicate-created",
])
def test_m3_malformed_record_refused_unchanged(tmp_path: Path, mutation: str) -> None:
    root, _new = _preparing_state(tmp_path, _UPGRADE, f"m3-{mutation}")
    marker = root / TRANSACTION_MARKER
    if mutation == "truncated":
        marker.write_text('{"schema_version": 2, "phase": "preparing"', encoding="utf-8")
    elif mutation == "nonobject":
        marker.write_text("[]", encoding="utf-8")
    elif mutation == "missing-key":
        _rewrite_record(root, lambda record: record.pop("backups"))
    elif mutation == "extra-key":
        _rewrite_record(root, lambda record: record.update({"bogus": 1}))
    elif mutation == "bool-schema":
        _rewrite_record(root, lambda record: record.update({"schema_version": True}))
    elif mutation == "unknown-version":
        _rewrite_record(root, lambda record: record.update({"schema_version": 3}))
    elif mutation == "unknown-phase":
        _rewrite_record(root, lambda record: record.update({"phase": "sideways"}))
    elif mutation == "bad-attempt":
        _rewrite_record(root, lambda record: record.update({"attempt_id": "FOREIGN"}))
    elif mutation == "unit-not-string":
        _rewrite_record(root, lambda record: record.update({"published_units": [{}]}))
    elif mutation == "duplicate-created":
        _rewrite_record(root, lambda record: record.update(
            {"created_parents": ["opt", "opt"]}))
    marker.chmod(0o600)
    _refuse_twice(root, _snapshot(root))


# M4: plausible marker with wrong identity / traversal / foreign ownership.

def test_m4_wrong_root_identity_refused_unchanged(tmp_path: Path) -> None:
    root, _new = _preparing_state(tmp_path, _UPGRADE, "m4-root")
    _rewrite_record(root, lambda record: record.update({"root": "/somewhere/else"}))
    _refuse_twice(root, _snapshot(root))


def test_m4_traversal_stage_refused_unchanged(tmp_path: Path) -> None:
    root, _new = _preparing_state(tmp_path, _UPGRADE, "m4-traversal")
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    attempt = record["attempt_id"]
    outside = root.parent / f".happyranch-stage-{attempt}-abcdefgh"
    outside.mkdir(mode=0o700, exist_ok=True)
    (outside / "sentinel").write_bytes(b"FOREIGN")
    _rewrite_record(root, lambda item: item.update({"stage": str(outside)}))
    _refuse_twice(root, _snapshot(root))
    assert (outside / "sentinel").read_bytes() == b"FOREIGN"


def test_m4_foreign_created_parent_refused_unchanged(tmp_path: Path) -> None:
    root, _new = _preparing_state(tmp_path, _UPGRADE, "m4-parent")
    foreign = root / "foreign-empty"
    foreign.mkdir(mode=0o710)
    _rewrite_record(root, lambda record: record["created_parents"].append("foreign-empty"))
    _refuse_twice(root, _snapshot(root))
    assert stat.S_IMODE(foreign.lstat().st_mode) == 0o710


# M5: missing / digest-mismatched backups and contradictory ownership facts.

def test_m5_digest_mismatch_refused_unchanged(tmp_path: Path) -> None:
    root, _new = _uncommitted_state(tmp_path, _UPGRADE, "m5-digest")
    _rewrite_record(root, lambda record: record["backups"]["payload"].update(
        {"sha256": "0" * 64}))
    _refuse_twice(root, _snapshot(root))


def test_m5_contradictory_prior_flag_refused_unchanged(tmp_path: Path) -> None:
    root, _new = _uncommitted_state(tmp_path, _UPGRADE, "m5-flag")
    _rewrite_record(root, lambda record: record.update({"payload_present": False}))
    _refuse_twice(root, _snapshot(root))


def test_m5_impossible_committed_progress_refused_unchanged(tmp_path: Path) -> None:
    root, _new = _uncommitted_state(tmp_path, _UPGRADE, "m5-progress")
    _rewrite_record(root, lambda record: record.update(
        {"phase": "committed", "published_units": []}))
    _refuse_twice(root, _snapshot(root))


def test_m5_missing_backup_with_recorded_presence_refused_unchanged(tmp_path: Path) -> None:
    root, _new = _uncommitted_state(tmp_path, _UPGRADE, "m5-missing")
    import shutil as _shutil
    _shutil.rmtree(root / _PAYLOAD_BACKUP_NAME)
    _refuse_twice(root, _snapshot(root))


# M6: no marker plus orphan residue, including empty owned-looking directories.

@pytest.mark.parametrize("residue", [
    "orphan-stage", "empty-payload-backup", "payload-backup", "empty-unit-backup",
    "unit-backup", "empty-dropin-backup", "dropin-backup", "record-temp",
])
def test_m6_unrecorded_residue_refused_unchanged(tmp_path: Path, residue: str) -> None:
    old, _new = _packages()
    root = tmp_path / f"m6-{residue}" / "root"
    root.mkdir(parents=True)
    if residue == "orphan-stage":
        stage = root / ".happyranch-stage-deadbeef"
        stage.mkdir(mode=0o700)
        (stage / "partial").write_bytes(b"x")
    elif residue == "empty-payload-backup":
        (root / _PAYLOAD_BACKUP_NAME).mkdir(mode=0o700)
    elif residue == "payload-backup":
        backup = root / _PAYLOAD_BACKUP_NAME
        backup.mkdir(mode=0o700)
        (backup / "old").write_bytes(b"y")
    elif residue == "empty-unit-backup":
        (root / _UNIT_BACKUP_NAME).mkdir(mode=0o700)
    elif residue == "unit-backup":
        backup = root / _UNIT_BACKUP_NAME
        backup.mkdir(mode=0o700)
        (backup / "old").write_bytes(b"y")
    elif residue == "empty-dropin-backup":
        backup = root / _UNIT_BACKUP_NAME / "happyranch-tsnet-sidecar.service.d"
        backup.mkdir(parents=True, mode=0o700)
    elif residue == "dropin-backup":
        backup = root / _UNIT_BACKUP_NAME / "happyranch-tsnet-sidecar.service.d"
        backup.mkdir(parents=True, mode=0o700)
        (backup / "10-enrollment-credential.conf").write_bytes(b"y")
    elif residue == "record-temp":
        (root / (TRANSACTION_MARKER + ".tmp")).write_bytes(b"FOREIGN-TEMP")
    before = _snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _snapshot(root) == before


def test_m6_truly_clean_root_remains_installable(tmp_path: Path) -> None:
    old, new = _packages()
    root = tmp_path / "m6-clean" / "root"
    root.mkdir(parents=True)
    (root / "unrelated.txt").write_bytes(b"keep")
    install_linux_package(new, root)
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-NEW"


# M7: foreign similarly named residue alongside owned state.

def test_m7_foreign_stage_sibling_preserved_on_recovery(tmp_path: Path) -> None:
    root, _new = _uncommitted_state(tmp_path, _UPGRADE, "m7-stage")
    foreign = root / ".happyranch-stage-foreign-sentinel"
    foreign.mkdir(mode=0o750)
    sentinel = foreign / "sentinel"
    sentinel.write_bytes(b"FOREIGN")
    sentinel.chmod(0o640)
    _recover_interrupted(root)
    assert sentinel.read_bytes() == b"FOREIGN"
    assert stat.S_IMODE(foreign.lstat().st_mode) == 0o750
    assert not list(root.glob(".happyranch-backup"))
    assert not (root / TRANSACTION_MARKER).exists()


# M8: symlink / wrong type at every owned path.

def test_m8_payload_backup_symlink_refused_and_target_unchanged(tmp_path: Path) -> None:
    root, _new = _uncommitted_state(tmp_path, _UPGRADE, "m8-payload-symlink")
    import shutil as _shutil
    _shutil.rmtree(root / _PAYLOAD_BACKUP_NAME)
    external = tmp_path / "m8-payload-symlink" / "external"
    external.mkdir()
    (external / "sentinel").write_bytes(b"EXTERNAL")
    (root / _PAYLOAD_BACKUP_NAME).symlink_to(external, target_is_directory=True)
    before = _snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _snapshot(root) == before
    assert (external / "sentinel").read_bytes() == b"EXTERNAL"


def test_m8_stage_symlink_refused_and_target_unchanged(tmp_path: Path) -> None:
    root, _new = _preparing_state(tmp_path, _UPGRADE, "m8-stage-symlink")
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    import shutil as _shutil
    _shutil.rmtree(record["stage"])
    external = tmp_path / "m8-stage-symlink" / "external"
    external.mkdir()
    (external / "sentinel").write_bytes(b"EXTERNAL")
    (root / Path(record["stage"]).name).symlink_to(external, target_is_directory=True)
    before = _snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _snapshot(root) == before
    assert (external / "sentinel").read_bytes() == b"EXTERNAL"


# M9: K before the first durable ownership record preserves/refuses the orphan.

@pytest.mark.parametrize(("operation", "stage"), [
    ("stage_create", "after"),
    ("record_temp_create", "before"),
    ("record_temp_write", "after"),
    ("record_replace", "before"),
])
def test_m9_interruption_before_first_record_preserves_orphan(
    tmp_path: Path, operation: str, stage: str,
) -> None:
    old, new = _packages()
    root = tmp_path / f"m9-{operation}-{stage}" / "root"
    _build_base(root, _UPGRADE, old)
    before = _snapshot(root)
    guard = _SeamGuard([_Rule(operation=operation, stage=stage, occurrence=1,
                              exception=_Interrupted)])
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=guard)
    observed = _snapshot(root)
    assert _old_artifacts_untouched(observed, before)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _snapshot(root) == observed


def test_m9_interruption_before_stage_allocation_is_clean(tmp_path: Path) -> None:
    old, new = _packages()
    root = tmp_path / "m9-clean" / "root"
    _build_base(root, _UPGRADE, old)
    guard = _SeamGuard([_Rule(operation="stage_create", stage="before", occurrence=1,
                              exception=_Interrupted)])
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=guard)
    # No residue was created, so recovery is an ordinary clean no-op.
    _recover_interrupted(root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-NEW"
