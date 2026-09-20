"""TASK8446 installer filesystem causal matrix (seq246 A/B/C; TASK8468 finding 4).

This module enumerates, for each declared fixture configuration, the finite
set of real installer filesystem operations accepted in
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
reinstalls.  Coverage is claimed only over the enumerated configurations in
``_CONFIGS``; combinations whose operations cannot occur for a configuration
are recorded as inapplicable in the task operation map rather than counted as
executed.

Fault modes per accepted section B: ``E`` = ``OSError`` raised before/after
the saved binding, ``P`` = a real partial destination write/copy followed by
``OSError``, and ``K`` = a one-shot ``BaseException`` escaping ordinary
cleanup.  A ``K`` (or partial residue) before the first durable ownership
record is unrecorded preparation residue: it is preserved and refused (M9),
never mistaken for a coherent owned transaction.  A ``persistent`` ``E``
raises on every matching occurrence while present and is cleared before the
successful recovery.

TASK8500 F4 corrections layered onto this same finite enumeration (no new
proof framework):

* publication partial destination writes now include the real
  ``record_temp_create``/``record_temp_write`` record seams at every reachable
  occurrence, in addition to the payload/unit/drop-in copies and the recovery
  partial cases;
* the finite prior-existence configurations cover each of the three prior
  units absent *individually* (the first+last-absent-together fixture is not a
  substitute) and a no-root upgrade with a genuine prior
  drop-in/directory/sibling preservation branch;
* the immediate post-commit oracle compares the complete active NEW
  composition — payload tree, every unit, drop-in, modes/types and preserved
  siblings — and permits only explicitly owned transaction residue (never a
  broad prefix exclusion), before recovery/reinstall and again after direct
  committed recovery; and
* the misleading M1 committed control is renamed to what it actually tests and
  a genuine committed-record/partial-cleanup positive control is added; and
* every case asserts the exact operation/stage/occurrence and real destination
  path it fired on (the randomly suffixed stage directory is matched by its
  exact stage shape), so no whole family is mapped to a representative.
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
    _STAGE_PREFIX,
    _UNIT_BACKUP_NAME,
    _inventory_tree,
    _recover_interrupted,
    _record_temp,
    _transaction_paths,
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
    """One accepted fixture configuration.

    ``enrollment`` selects whether the *new* install has an enrollment source
    (and therefore publishes a transient drop-in).  ``prior_units``,
    ``prior_dropin`` and ``prior_dropin_dir`` shape the upgrade baseline so the
    finite reachable prior-existence branches are exercised without inventing
    unreachable combinations.
    """

    def __init__(self, name: str, *, upgrade: bool, system_service: bool,
                 enrollment: bool, prior_units: object = "all",
                 prior_dropin: bool = False, prior_dropin_dir: bool = False) -> None:
        self.name = name
        self.upgrade = upgrade
        self.system_service = system_service
        self.enrollment = enrollment
        self._prior_units = prior_units
        self.prior_dropin = prior_dropin
        self.prior_dropin_dir = prior_dropin_dir

    @property
    def prior_unit_set(self) -> tuple[str, ...]:
        if self._prior_units == "all":
            return tuple(UNITS)
        if self._prior_units == "none":
            return ()
        return tuple(self._prior_units)


_CONFIGS = (
    _Config("upgrade-noroot", upgrade=True, system_service=False, enrollment=False),
    _Config("fresh-noroot", upgrade=False, system_service=False, enrollment=False),
    _Config("upgrade-system", upgrade=True, system_service=True, enrollment=True,
            prior_dropin=True, prior_dropin_dir=True),
    _Config("upgrade-system-nodropin", upgrade=True, system_service=True,
            enrollment=True, prior_dropin=False, prior_dropin_dir=False),
    _Config("upgrade-system-noenroll-priordropin", upgrade=True, system_service=True,
            enrollment=False, prior_dropin=True, prior_dropin_dir=True),
    _Config("upgrade-system-noenroll-nodropin", upgrade=True, system_service=True,
            enrollment=False, prior_dropin=False, prior_dropin_dir=False),
    _Config("upgrade-noroot-unitabsent", upgrade=True, system_service=False,
            enrollment=False, prior_units=(UNITS[1],)),
    # Each of the three prior units absent individually.  The first+last
    # absent-together configuration above is not a substitute for these.
    _Config("upgrade-noroot-firstabsent", upgrade=True, system_service=False,
            enrollment=False, prior_units=(UNITS[1], UNITS[2])),
    _Config("upgrade-noroot-secondabsent", upgrade=True, system_service=False,
            enrollment=False, prior_units=(UNITS[0], UNITS[2])),
    _Config("upgrade-noroot-lastabsent", upgrade=True, system_service=False,
            enrollment=False, prior_units=(UNITS[0], UNITS[1])),
    # No-root upgrade with a genuine prior drop-in/directory/sibling: the
    # no-new-dropin preservation branch must still be exercised and retained.
    _Config("upgrade-noroot-priordropin", upgrade=True, system_service=False,
            enrollment=False, prior_dropin=True, prior_dropin_dir=True),
    _Config("fresh-system", upgrade=False, system_service=True, enrollment=True),
    _Config("fresh-system-noenroll", upgrade=False, system_service=True, enrollment=False),
)


def _build_base(root: Path, config: _Config, old: Path) -> None:
    if config.system_service:
        _stage_system_credentials(root, enrollment=config.enrollment)
    if config.upgrade:
        install_linux_package(old, root, system_service=config.system_service)
    else:
        root.mkdir(parents=True, exist_ok=True)
        (root / "unrelated.txt").write_bytes(b"keep")
    _shape_prior(root, config)


def _shape_prior(root: Path, config: _Config) -> None:
    """Apply the declared prior-existence branch with loss-detecting bytes/modes.

    Upgrade baselines carry three distinguishable prior unit bodies/modes
    (``0600/0640/0644``) and a distinct operator drop-in (``0640`` file,
    ``0710`` directory) with an unrelated sibling.  Absent units are removed
    from the real OLD baseline so the recorded prior-absence branch is genuine.
    """
    if not config.upgrade:
        return
    present = set(config.prior_unit_set)
    for index, unit in enumerate(UNITS):
        target = root / "etc/systemd/system" / unit
        if unit in present:
            target.write_bytes(f"OLD-unit-{index}-{unit}".encode())
            target.chmod((0o600, 0o640, 0o644)[index])
        elif target.exists() or target.is_symlink():
            target.unlink()
    # The prior drop-in branch is shaped for both install modes: a no-root
    # upgrade may still carry an operator-managed prior drop-in that the new
    # install never publishes but must preserve.
    dropin_dir = root / "etc/systemd/system/happyranch-tsnet-sidecar.service.d"
    dropin = dropin_dir / "10-enrollment-credential.conf"
    if config.prior_dropin:
        dropin_dir.mkdir(parents=True, exist_ok=True)
        dropin_dir.chmod(0o710)
        dropin.write_bytes(b"operator-managed-prior-dropin\n")
        dropin.chmod(0o640)
        sibling = dropin_dir / "99-foreign-sibling.conf"
        sibling.write_bytes(b"foreign-sibling\n")
        sibling.chmod(0o600)
    else:
        if dropin.exists() or dropin.is_symlink():
            dropin.unlink()
        if not config.prior_dropin_dir and dropin_dir.exists():
            shutil.rmtree(dropin_dir)


# ---------------------------------------------------------------------------
# Real binding fault injectors
# ---------------------------------------------------------------------------

class _Rule:
    def __init__(self, *, operation: str, stage: str, occurrence: int,
                 exception: type[BaseException], partial: bool = False,
                 persistent: bool = False) -> None:
        self.operation = operation
        self.stage = stage
        self.occurrence = occurrence
        self.exception = exception
        self.partial = partial
        self.persistent = persistent
        self.seen = 0
        self.raises = 0
        # The exact real path the fault fired on, so every case can prove the
        # operation/stage/occurrence/path binding it claims.
        self.fired_path: str | None = None
        self.fired_seen: int | None = None


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
        rule.fired_path = path
        rule.fired_seen = rule.seen
        raise rule.exception(FAILURE)

    def _match(self, rule: _Rule, stage: str, operation: str, path: str) -> bool:
        if stage != rule.stage or operation != rule.operation:
            return False
        rule.seen += 1
        if not rule.persistent and rule.seen != rule.occurrence:
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
                   "unit_backup", "dropin_backup", "record_temp_create", "record_temp_write")


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


def _path_binding_matches(expected_name: str, fired_path: str | None) -> bool:
    """The fault fired on the destination the case actually names.

    The allocated stage directory carries a fresh random suffix on every run,
    so its basename is compared by exact stage shape instead; every other
    operation name binds its literal destination basename.
    """
    if fired_path is None:
        return False
    fired_name = Path(fired_path).name
    if expected_name.startswith(_STAGE_PREFIX):
        return fired_name.startswith(_STAGE_PREFIX)
    return fired_name == expected_name


_WORK = _DISCOVERY_ROOT / "discovery"
_CACHE: dict[str, dict] = {}


def _prior_identities(template: Path, config: _Config) -> dict:
    """Distinguishable prior identities for the loss-detecting immediate oracle."""
    old_units: dict[str, dict] = {}
    absent_units: list[str] = []
    if config.upgrade:
        for unit in UNITS:
            path = template / "etc/systemd/system" / unit
            if path.is_file():
                old_units[unit] = {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "mode": stat.S_IMODE(path.lstat().st_mode),
                }
            else:
                absent_units.append(unit)
    dropin = None
    dropin_path = (template / "etc/systemd/system/happyranch-tsnet-sidecar.service.d"
                   / "10-enrollment-credential.conf")
    if dropin_path.is_file():
        dropin = {
            "sha256": hashlib.sha256(dropin_path.read_bytes()).hexdigest(),
            "mode": stat.S_IMODE(dropin_path.lstat().st_mode),
        }
    return {"old_units": old_units, "absent_units": tuple(absent_units), "dropin": dropin}


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
        prior = _prior_identities(template, config)
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
        **prior,
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

_RECOVERY_CONFIGS = tuple(_CONFIGS)

# The only real content-write destinations on the rollback/recovery route: a
# partial destination write is meaningful there, while ``*_restore`` uses
# ``os.replace`` and ``*_unlink``/``*_rmdir`` remove (their gradual effects are
# the distinct occurrences already enumerated).
_RECOVERY_WRITE_OPERATIONS = frozenset({"record_temp_create", "record_temp_write"})


def _recovery_cache(config: _Config) -> dict:
    key = "recovery:" + config.name
    if key in _CACHE:
        return _CACHE[key]
    old, new = _packages()
    base = _WORK / ("recovery-" + config.name)
    template = base / "template"
    _build_base(template, config, old)
    old_snapshot = _snapshot(template)
    old_payload = (
        _inventory_tree(template / "opt/happyranch")
        if (template / "opt/happyranch").is_dir() else None
    )
    prior = _prior_identities(template, config)
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
        "trace": recording.trace,
        "ranks": _ranks(recording.trace),
        **prior,
    }
    return _CACHE[key]


class _RRCacheUnset(Exception):
    pass


class _RRCase:
    def __init__(self, cache: dict, index: int, route: str, mode: str,
                 persistent: bool = False) -> None:
        self.cache = cache
        self.index = index
        self.route = route
        self.mode = mode
        self.persistent = persistent
        stage, operation, path = cache["trace"][index]
        self.stage = stage
        self.operation = operation
        self.relpath = Path(path).name
        self.occurrence = cache["ranks"][index]
        suffix = "-persistent" if persistent else ""
        self.id = (
            f"{cache['config'].name}-{route}-{mode}{suffix}-{operation}-{stage}"
            f"-{self.occurrence}"
        )


def _recovery_cases() -> list[_RRCase]:
    cases: list[_RRCase] = []
    for config in _RECOVERY_CONFIGS:
        cache = _recovery_cache(config)
        for index in range(len(cache["trace"])):
            operation = cache["trace"][index][1]
            occurrence = cache["ranks"][index]
            modes = ["E", "K"]
            if operation in _RECOVERY_WRITE_OPERATIONS:
                modes.append("P")
            for route in ("RB", "REC"):
                for mode in modes:
                    cases.append(_RRCase(cache, index, route, mode))
                    if mode == "E" and occurrence == 1:
                        # Persistent fault at the first real occurrence of every
                        # distinct rollback/recovery operation.
                        cases.append(_RRCase(cache, index, route, mode, persistent=True))
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
    if "config" in metafunc.fixturenames:
        configs = list(_CONFIGS)
        metafunc.parametrize("config", configs, ids=[config.name for config in configs])


# ---------------------------------------------------------------------------
# Oracles
# ---------------------------------------------------------------------------

def _old_artifacts_untouched(state: dict, old: dict) -> bool:
    return all(rel in state and state[rel] == value for rel, value in old.items())


def _payload_is_new(root: Path, new_root: Path) -> bool:
    opt = root / "opt/happyranch"
    expected = new_root / "opt/happyranch"
    return opt.is_dir() and not opt.is_symlink() and _inventory_tree(opt) == _inventory_tree(expected)


def _assert_complete_active_new(root: Path, expected: dict) -> None:
    """Every artifact of a clean NEW install must already be present and exact.

    This is the complete immediate committed oracle: it compares the payload
    tree, every published unit, the drop-in (or its preserved prior bytes) and
    every preserved sibling by lstat type, bytes, mode and uid/gid against the
    independently produced clean NEW snapshot.  ``_payload_is_new`` covers only
    ``opt/happyranch``, so it can hide a mixed unit/drop-in state.
    """
    state = _snapshot(root)
    diverged = [relative for relative, value in expected.items() if state.get(relative) != value]
    assert not diverged, f"complete active NEW mismatch at: {diverged}"


def _owned_residue_prefixes(root: Path) -> tuple[str, ...]:
    payload_backup, unit_backup, marker = _transaction_paths(root)
    return tuple(
        str(path.relative_to(root))
        for path in (marker, _record_temp(root), payload_backup, unit_backup)
    )


def _assert_only_owned_residue(root: Path, expected: dict) -> None:
    """Every non-NEW path must be an explicitly owned transaction artifact.

    Only the exact recorded marker/temp/backup paths (or their descendants) are
    tolerated; a broad ``.happyranch-*`` prefix exclusion is intentionally not
    used, so an unrelated or foreign sibling still fails.
    """
    owned = _owned_residue_prefixes(root)
    for relative in _snapshot(root):
        if relative in expected:
            continue
        if any(relative == prefix or relative.startswith(prefix + "/") for prefix in owned):
            continue
        raise AssertionError(f"unexpected unowned residue {relative!r}")


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


def _assert_old_evidence(root: Path, cache: dict) -> None:
    """Every prior artifact must remain reachable in its active or backup place.

    This is the immediate-state oracle applied *before* the recovery retry, so
    an eventual-equal snapshot cannot hide a prior byte/mode that was already
    destroyed by the fault.
    """
    if cache["old_payload"] is not None:
        active = root / "opt/happyranch"
        backup = root / _PAYLOAD_BACKUP_NAME
        assert _tree_matches(active, cache["old_payload"]) or _tree_matches(
            backup, cache["old_payload"]
        )
    unit_backup = root / _UNIT_BACKUP_NAME
    for unit, identity in cache["old_units"].items():
        assert _prior_artifact_retained(
            root, f"etc/systemd/system/{unit}", identity, unit_backup
        )
    if cache["dropin"] is not None:
        assert _prior_artifact_retained(
            root,
            "etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf",
            cache["dropin"],
            unit_backup / "happyranch-tsnet-sidecar.service.d",
        )


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
    # An unrecorded orphan exists only when the interruption escapes the
    # ordinary handler (K) before the first durable record; a caught OSError
    # (E/P) triggers the product's own pre-record cleanup and returns to OLD.
    unrecorded = (
        pre_commit
        and pub_case.index < cache["first_record"]
        and pub_case.index >= 1
        and pub_case.mode == "K"
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
    # Exact binding: the fault really hit the case's own operation/stage
    # occurrence and the real destination path it names, never a sibling
    # occurrence or a different artifact with the same operation name.  The
    # guard keeps observing the product's own recovery seams after the
    # injected failure, so ``seen`` may exceed the fire occurrence.
    assert rule.fired_seen == pub_case.occurrence
    assert _path_binding_matches(pub_case.relpath, rule.fired_path)

    if unrecorded:
        # M9: no durable ownership record yet; the unknown orphan is preserved
        # and refused, and no OLD artifact changed.  The full snapshot is
        # re-checked after *each* refusal, not only after both.
        observed = _snapshot(case)
        assert _old_artifacts_untouched(observed, cache["old"])
        for _ in range(2):
            with pytest.raises(PackageError, match="transaction_state_invalid"):
                _recover_interrupted(case)
            assert _snapshot(case) == observed
        return

    # Immediate-state oracle, before any recovery retry.  An ordinary
    # ``OSError`` is caught by install's own handler, so the state is already
    # OLD (pre-commit) or preserved NEW (post-commit); a ``K`` escapes that
    # handler, so the intermediate evidence must be observable here.
    if pub_case.mode in ("E", "P"):
        if pre_commit:
            assert _snapshot(case) == cache["old"]
        else:
            _assert_complete_active_new(case, cache["new"])
            _assert_only_owned_residue(case, cache["new"])
    elif pre_commit:
        _assert_old_evidence(case, cache)
    else:
        # A K after the authoritative commit escapes ordinary cleanup; the
        # complete committed NEW composition (payload, every unit, drop-in,
        # modes/types and preserved siblings) must already be authoritative,
        # with only explicitly owned transaction residue remaining.
        _assert_complete_active_new(case, cache["new"])
        _assert_only_owned_residue(case, cache["new"])

    _recover_interrupted(case)
    if pre_commit:
        assert _snapshot(case) == cache["old"]
    else:
        # After direct committed recovery the complete active NEW state and
        # only it must remain, before any reinstall can mask a mixed state.
        assert _snapshot(case) == cache["new"]
    install_linux_package(new, case, system_service=config.system_service)
    assert _snapshot(case) == cache["new"]
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
        partial=rr_case.mode == "P",
        persistent=rr_case.persistent,
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

    if rr_case.persistent:
        # A persistent fault fires on every real occurrence it reaches while
        # present.  OLD evidence must survive; after the fault clears the real
        # install entry point performs the recovery itself.
        assert recovery_rule.raises >= 1
        assert recovery_rule.raises == recovery_rule.seen
        _assert_old_evidence(case, cache)
        install_linux_package(new, case, system_service=config.system_service)
        assert _snapshot(case) == cache["new"]
        install_linux_package(new, case, system_service=config.system_service)
        assert _snapshot(case) == cache["new"]
        return

    assert recovery_rule.raises == 1
    # Exact binding on the recovery route too: the case's own stage/operation
    # occurrence and its real destination path, not a same-named sibling.
    assert recovery_rule.fired_seen == rr_case.occurrence
    assert _path_binding_matches(rr_case.relpath, recovery_rule.fired_path)

    # While faulted, every OLD byte/mode remains reachable in its active or
    # recorded backup location (the immediate-state oracle before recovery).
    _assert_old_evidence(case, cache)

    _recover_interrupted(case)
    assert _snapshot(case) == cache["old"]
    install_linux_package(new, case, system_service=config.system_service)
    assert _snapshot(case) == cache["new"]
    install_linux_package(new, case, system_service=config.system_service)
    assert _snapshot(case) == cache["new"]


def test_install_recovery_call_path(tmp_path: Path, config: _Config) -> None:
    """The shipping install entry point performs its own recovery.

    Direct ``_recover_interrupted`` before both installs cannot demonstrate
    that ``install_linux_package`` recovers an interrupted state, so this test
    leaves the interrupted state in place and asserts that install's internal
    recovery ran (real ``rollback_*`` operations) and reached NEW.
    """
    cache = _recovery_cache(config)
    _old, new = _packages()
    case = tmp_path / "root"
    shutil.copytree(cache["template"], case, symlinks=True)
    _build_interrupted_state(case, config, new)
    recording = _SeamGuard()
    install_linux_package(new, case, system_service=config.system_service, guard=recording)
    operations = {operation for _stage, operation, _path in recording.trace}
    assert any(operation.startswith("rollback_") for operation in operations)
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
        # The full marker/foreign/external state is unchanged after *each*
        # refusal, not only after both.
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


def test_m1_interrupted_final_unit_write_restores_old(tmp_path: Path) -> None:
    """An interrupted *final-unit write* is pre-commit, not a committed control.

    The prior name implied committed authority; the real binding fires before
    the authoritative commit, so recovery must restore OLD.  The genuine
    committed positive control is the separate test below.
    """
    old, new = _packages()
    root = tmp_path / "m1-committed" / "root"
    _build_base(root, _UPGRADE, old)
    trigger = _publication_rule(_UPGRADE, _Interrupted)
    assert trigger.operation == f"unit_publish:{UNITS[-1]}"
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=_SeamGuard(armed=False, trigger=trigger))
    # The record is real but pre-commit: recovery restores the complete OLD.
    _recover_interrupted(root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-OLD"
    assert (root / "etc/systemd/system" / UNITS[-1]).read_bytes().startswith(b"OLD-unit-2-")
    assert not list(root.glob(".happyranch-*"))
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-NEW"
    assert not list(root.glob(".happyranch-*"))


def test_m1_genuine_committed_record_survives_partial_cleanup(tmp_path: Path) -> None:
    """A genuine committed record plus partial committed cleanup retains NEW.

    Interrupting the final ``marker_remove`` cleanup step leaves the durable
    ``committed`` record with the complete active NEW installation and only
    explicitly owned residue; recovery validates that composition and finishes
    the cleanup without deleting any active NEW artifact.
    """
    old, new = _packages()
    root = tmp_path / "m1-genuine-committed" / "root"
    _build_base(root, _UPGRADE, old)
    rule = _Rule(operation="marker_remove", stage="before", occurrence=1,
                 exception=_Interrupted)
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=_SeamGuard([rule]))
    assert rule.raises == 1
    assert json.loads((root / TRANSACTION_MARKER).read_text())["phase"] == "committed"
    cache = _config_cache(_UPGRADE)
    _assert_complete_active_new(root, cache["new"])
    _assert_only_owned_residue(root, cache["new"])
    _recover_interrupted(root)
    assert _snapshot(root) == cache["new"]
    install_linux_package(new, root)
    assert _snapshot(root) == cache["new"]
    install_linux_package(new, root)
    assert _snapshot(root) == cache["new"]


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
