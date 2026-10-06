"""Causal installer transaction regressions (TASK8446 seq246 sections A/B/C).

These tests fault the *real* filesystem bindings (record temp write/replace,
payload/unit/drop-in publication, backup copy, restore renames and cleanup
unlinks) and assert durable bytes/modes rather than a helper's return value.
Every fault case requires an exact-one-hit receipt and an actual-mutation
oracle, matching the accepted finite P0-P5 / I1-I5 / C1-C3 / R1-R5 / M1-M9
families and the separate exception-rollback (RB) and interruption-recovery
(REC) routes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import unittest.mock
import zipfile
from pathlib import Path

import pytest

from runtime.remote_access.linux_package import (
    PackageError,
    TRANSACTION_MARKER,
    UNITS,
    _PAYLOAD_BACKUP_NAME,
    _UNIT_BACKUP_NAME,
    _assert_committed_active_new,
    _inventory_tree,
    _recover_interrupted,
    _record_temp,
    build_linux_package,
    install_linux_package,
)
from tests.remote_access.test_linux_package import (
    _InstallerGuard,
    _distinct_package,
    _inputs,
    _installer_snapshot,
    _rewrite_package,
    _stage_system_credentials,
)


class _Interrupted(BaseException):
    """A one-shot process interruption that escapes ordinary rollback."""


def _old_new(tmp_path: Path) -> tuple[Path, Path]:
    return (
        _distinct_package(tmp_path, "1", b"old"),
        _distinct_package(tmp_path, "2", b"new"),
    )


def _distinct_unit_package(tmp_path: Path, version: str, marker: bytes) -> Path:
    """A package whose OLD/NEW rendered unit bodies and payload bytes all differ."""
    package = _distinct_package(tmp_path, version, marker)

    def mutate(entries) -> None:
        manifest_index = next(
            index for index, (member, _raw) in enumerate(entries)
            if member.name.endswith("manifest.json")
        )
        manifest_member, manifest_raw = entries[manifest_index]
        manifest = json.loads(manifest_raw)
        for unit in UNITS:
            suffix = f"systemd/{unit}"
            index = next(
                position for position, (member, _raw) in enumerate(entries)
                if member.name.endswith(suffix)
            )
            member, raw = entries[index]
            mutated = raw + f"# {marker.decode()}\n".encode()
            entries[index] = (member, mutated)
            next(
                item for item in manifest["files"] if item["path"] == suffix
            )["sha256"] = hashlib.sha256(mutated).hexdigest()
        entries[manifest_index] = (manifest_member, json.dumps(manifest).encode())

    return _rewrite_package(package, tmp_path / f"pkg-{version}-units.tar", mutate)


def _upgrade_root(tmp_path: Path, *, system_service: bool = False, enrollment: bool = False) -> Path:
    old, _new = _old_new(tmp_path)
    root = tmp_path / "root"
    if system_service:
        _stage_system_credentials(root, enrollment=enrollment)
    install_linux_package(old, root, system_service=system_service)
    return root


class _RecordPhaseGuard:
    """Fire on a record seam only while the bound file holds a chosen phase.

    Unlike ``_InstallerGuard`` this inspects the actual record bytes, so it can
    target the authoritative commit publication specifically rather than the
    first record update.
    """

    def __init__(self, *, operation: str, stage: str, phase: str,
                 exception: type[BaseException] = OSError, truncate: bool = False) -> None:
        self.operation, self.stage, self.phase = operation, stage, phase
        self.exception, self.truncate = exception, truncate
        self.fired = 0

    def __call__(self, stage: str, operation: str, path: str) -> None:
        if stage != self.stage or operation != self.operation:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or payload.get("phase") != self.phase:
            return
        self.fired += 1
        if self.truncate:
            Path(path).write_text('{"phase": "committed"', encoding="utf-8")
        raise self.exception("injected")


def _replace_interrupter(src_name: str, exception: type[BaseException]):
    """Return (wrapper, state) that raises once after a matching real os.replace."""
    original = os.replace
    state = {"fired": 0}

    def wrapper(src, dst):
        result = original(src, dst)
        if state["fired"] == 0 and Path(src).name == src_name:
            state["fired"] += 1
            raise exception("interrupted-after-restore")
        return result

    return wrapper, state


# ---------------------------------------------------------------------------
# A. Commit authority owns the decision (I5, finding 1)
# ---------------------------------------------------------------------------

def test_commit_failure_before_publication_restores_old(tmp_path: Path) -> None:
    """Failure before the committed record reaches disk must restore OLD."""
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)
    before = _installer_snapshot(root)
    guard = _RecordPhaseGuard(operation="record_temp_write", stage="after", phase="committed")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert guard.fired == 1
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))


def test_commit_publication_failure_after_replace_preserves_new(tmp_path: Path) -> None:
    """Failure after the committed record reached disk must preserve complete NEW."""
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)
    guard = _RecordPhaseGuard(operation="record_replace", stage="after", phase="committed")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert guard.fired == 1
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not (root / TRANSACTION_MARKER).exists()
    assert not list(root.glob(".happyranch-*"))
    # A clean re-entry installs and stays NEW.
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"


def test_truncated_record_temp_never_becomes_authority(tmp_path: Path) -> None:
    """A torn record temp write must leave the previous durable record authoritative."""
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)
    before = _installer_snapshot(root)
    guard = _RecordPhaseGuard(
        operation="record_temp_write", stage="after", phase="committed", truncate=True
    )
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert guard.fired == 1
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))


# ---------------------------------------------------------------------------
# B. Complete payload identity (finding 3, M5)
# ---------------------------------------------------------------------------

def _interrupt_after_payload_publish(tmp_path: Path, *, system_service: bool = False) -> Path:
    root = _upgrade_root(tmp_path, system_service=system_service)
    _old, new = _old_new(tmp_path)

    def interrupt(name: str) -> None:
        if name == "payload_published":
            raise _Interrupted("payload-published")

    with pytest.raises(_Interrupted):
        install_linux_package(new, root, system_service=system_service, fault=interrupt)
    return root


def test_corrupt_payload_backup_member_is_refused_unchanged(tmp_path: Path) -> None:
    root = _interrupt_after_payload_publish(tmp_path)
    backup = root / _PAYLOAD_BACKUP_NAME
    (backup / "bin/happyranch-tsnet-sidecar").write_bytes(b"CORRUPT")
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        _recover_interrupted(root)
    assert _installer_snapshot(root) == before
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"


def test_payload_backup_directory_mode_mismatch_is_refused(tmp_path: Path) -> None:
    root = _interrupt_after_payload_publish(tmp_path)
    os.chmod(root / _PAYLOAD_BACKUP_NAME / "share", 0o755)
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        _recover_interrupted(root)
    assert _installer_snapshot(root) == before


def test_missing_payload_backup_with_new_opt_is_refused(tmp_path: Path) -> None:
    root = _interrupt_after_payload_publish(tmp_path)
    shutil.rmtree(root / _PAYLOAD_BACKUP_NAME)
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        _recover_interrupted(root)
    assert _installer_snapshot(root) == before


def test_partial_payload_backup_is_refused(tmp_path: Path) -> None:
    root = _interrupt_after_payload_publish(tmp_path)
    (root / _PAYLOAD_BACKUP_NAME / "share/happyranch.whl").unlink()
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        _recover_interrupted(root)
    assert _installer_snapshot(root) == before


def test_recorded_fresh_absence_with_payload_backup_is_refused(tmp_path: Path) -> None:
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    guard = _InstallerGuard(operation="unit_publish:happyranch-managed.target", stage="after",
                            exception=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        install_linux_package(package, root, guard=guard)
    # Fabricate an unexplained payload backup under a recorded fresh-absence state.
    backup = root / _PAYLOAD_BACKUP_NAME
    backup.mkdir(mode=0o700)
    (backup / "stray").write_bytes(b"stray")
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        _recover_interrupted(root)
    assert _installer_snapshot(root) == before


# ---------------------------------------------------------------------------
# C. Resumable rollback and recovery (finding 2, R1-R5)
# ---------------------------------------------------------------------------

def test_exception_rollback_resumes_after_consumed_unit_backup(tmp_path: Path) -> None:
    """RB: a fault after the first unit restore must still permit full OLD + reinstalls."""
    old = _distinct_unit_package(tmp_path, "1", b"old")
    new = _distinct_unit_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)

    def fault(name: str) -> None:
        if name == "unit_published:happyranch-managed.target":
            raise RuntimeError("injected")

    wrapper, state = _replace_interrupter("happyranch-connector.service", _Interrupted)
    with unittest.mock.patch.object(os, "replace", wrapper):
        with pytest.raises(_Interrupted):
            install_linux_package(new, root, fault=fault)
    assert state["fired"] == 1
    # The first unit was really restored in place and its backup consumed; the
    # remaining OLD bytes/modes still live in their recorded backup locations.
    assert (root / _UNIT_BACKUP_NAME).exists()
    assert not (root / _UNIT_BACKUP_NAME / "happyranch-connector.service").exists()
    assert (root / TRANSACTION_MARKER).exists()
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not list(root.glob(".happyranch-*"))
    assert _installer_snapshot(root) != before


def test_recovery_resumes_after_consumed_payload_backup(tmp_path: Path) -> None:
    """REC: a fault after the payload restore must still permit full OLD restoration."""
    old = _distinct_unit_package(tmp_path, "1", b"old")
    new = _distinct_unit_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)

    def interrupt(name: str) -> None:
        if name == "unit_published:happyranch-managed.target":
            raise _Interrupted("published")

    with pytest.raises(_Interrupted):
        install_linux_package(new, root, fault=interrupt)
    wrapper, state = _replace_interrupter(_PAYLOAD_BACKUP_NAME, _Interrupted)
    with unittest.mock.patch.object(os, "replace", wrapper):
        with pytest.raises(_Interrupted):
            _recover_interrupted(root)
    assert state["fired"] == 1
    assert not (root / _PAYLOAD_BACKUP_NAME).exists()
    assert (root / TRANSACTION_MARKER).exists()
    _recover_interrupted(root)
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))


def test_persistent_rollback_fault_refuses_while_present_then_recovers(tmp_path: Path) -> None:
    """A persistent fault may block progress while present, never permanently."""
    old = _distinct_unit_package(tmp_path, "1", b"old")
    new = _distinct_unit_package(tmp_path, "2", b"new")
    root = tmp_path / "root"
    install_linux_package(old, root)
    before = _installer_snapshot(root)

    def interrupt(name: str) -> None:
        if name == "unit_published:happyranch-managed.target":
            raise _Interrupted("published")

    with pytest.raises(_Interrupted):
        install_linux_package(new, root, fault=interrupt)
    persistent = _InstallerGuard(
        operation="rollback_unit_restore", stage="before", persistent=True
    )
    with pytest.raises(OSError, match="injected"):
        _recover_interrupted(root, persistent)
    # OLD bytes/modes remain in their active or recorded backup locations.
    assert (root / _UNIT_BACKUP_NAME / "happyranch-managed.target").exists()
    # Fault cleared: full OLD restoration and two clean installs succeed.
    _recover_interrupted(root)
    assert _installer_snapshot(root) == before
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not list(root.glob(".happyranch-*"))


def test_prior_absent_partial_unit_write_is_removed_on_rollback(tmp_path: Path) -> None:
    """A torn write to a prior-absent unit is an owned NEW artifact (intent recorded)."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    (root / "unrelated.txt").write_bytes(b"keep")
    guard = _InstallerGuard(operation="unit_publish:happyranch-connector.service",
                            stage="before", partial=True)
    with pytest.raises(OSError, match="injected"):
        install_linux_package(package, root, guard=guard)
    assert len(guard.receipts()) == 1
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    assert not (root / "etc/systemd/system/happyranch-connector.service").exists()
    assert not (root / "opt").exists()
    assert not list(root.glob(".happyranch-*"))


# ---------------------------------------------------------------------------
# D. Ownership negatives (finding 4, M3-M9)
# ---------------------------------------------------------------------------

def test_unit_symlink_is_refused_and_sentinel_unchanged(tmp_path: Path) -> None:
    root = _upgrade_root(tmp_path)
    sentinel = tmp_path / "external"
    sentinel.write_bytes(b"FOREIGN")
    sentinel.chmod(0o640)
    unit = root / "etc/systemd/system/happyranch-connector.service"
    unit.unlink()
    unit.symlink_to(sentinel)
    _old, new = _old_new(tmp_path)
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        install_linux_package(new, root)
    assert sentinel.read_bytes() == b"FOREIGN"
    assert stat.S_IMODE(sentinel.lstat().st_mode) == 0o640
    assert _installer_snapshot(root) == before


def test_opt_symlink_is_refused_before_write(tmp_path: Path) -> None:
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    (root / "opt").mkdir(parents=True)
    external = tmp_path / "external-opt"
    external.mkdir()
    (root / "opt/happyranch").symlink_to(external)
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        install_linux_package(package, root)
    assert _installer_snapshot(root) == before
    assert not list(external.iterdir())


def test_marker_symlink_is_refused_unchanged(tmp_path: Path) -> None:
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    external = tmp_path / "external-marker"
    external.write_text("{}")
    (root / TRANSACTION_MARKER).symlink_to(external)
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        install_linux_package(package, root)
    assert _installer_snapshot(root) == before
    assert external.read_text() == "{}"


def test_duplicate_record_keys_are_refused(tmp_path: Path) -> None:
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)

    def interrupt(name: str) -> None:
        if name == "payload_published":
            raise _Interrupted("published")

    with pytest.raises(_Interrupted):
        install_linux_package(new, root, fault=interrupt)
    marker = root / TRANSACTION_MARKER
    original = json.loads(marker.read_text())
    marker.write_text(json.dumps(original)[:-1] + ', "phase": "committed"}')
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        _recover_interrupted(root)
    assert _installer_snapshot(root) == before


def test_stage_identity_not_bound_to_attempt_is_refused(tmp_path: Path) -> None:
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)

    def early(stage: str, operation: str, path: str) -> None:
        if stage == "before" and operation == "stage_payload:bin/happyranch-tsnet-sidecar":
            raise _Interrupted("preparing")

    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=early)
    marker = root / TRANSACTION_MARKER
    record = json.loads(marker.read_text())
    foreign = root / ".happyranch-stage-foreign"
    foreign.mkdir()
    (foreign / "sentinel").write_bytes(b"FOREIGN")
    record["attempt_id"] = "foreign"
    record["stage"] = str(foreign)
    marker.write_text(json.dumps(record))
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        _recover_interrupted(root)
    assert _installer_snapshot(root) == before
    assert (foreign / "sentinel").read_bytes() == b"FOREIGN"


def test_foreign_stage_sibling_is_preserved_when_owned_residue_is_cleaned(tmp_path: Path) -> None:
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)

    def interrupt(name: str) -> None:
        if name == "payload_published":
            raise _Interrupted("published")

    with pytest.raises(_Interrupted):
        install_linux_package(new, root, fault=interrupt)
    foreign = root / ".happyranch-stage-foreign"
    foreign.mkdir()
    (foreign / "sentinel").write_bytes(b"KEEP")
    # The foreign sibling does not match the recorded stage, so recovery still
    # restores OLD and leaves the foreign entry untouched.
    _recover_interrupted(root)
    assert (foreign / "sentinel").read_bytes() == b"KEEP"
    assert not list(root.glob(".happyranch-backup"))


def test_unsafe_unit_parent_symlink_is_refused(tmp_path: Path) -> None:
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    external = tmp_path / "external-etc"
    (external / "systemd" / "system").mkdir(parents=True)
    root.mkdir()
    (root / "etc").symlink_to(external)
    before = _installer_snapshot(root)
    with pytest.raises(PackageError, match="transaction_state_invalid"):
        install_linux_package(package, root)
    assert _installer_snapshot(root) == before
    assert not list((external / "systemd" / "system").iterdir())


# ---------------------------------------------------------------------------
# E. Operation/occurrence map with exact-one-hit receipts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("operation", "stage"), [
    ("stage_create", "after"),                     # P0 stage allocation
    ("stage_chmod", "after"),                      # P0 stage-root chmod
    ("stage_dir:mkdir", "after"),                  # P1 payload parent
    ("stage_payload:bin/happyranch-tsnet-sidecar", "after"),   # P1 first member
    ("stage_payload:share/happyranch.whl", "after"),           # P1 later member
    ("stage_manifest", "after"),                   # P2 staged manifest
    ("unit_backup:happyranch-connector.service", "after"),     # P3 first copy
    ("unit_backup:happyranch-managed.target", "after"),        # P3 later copy
    ("payload_retain", "after"),                   # I1 upgrade retain
    ("payload_publish", "after"),                  # I2 stage->opt rename
    ("unit_publish:happyranch-tsnet-sidecar.service", "after"),  # I3 unit write
])
def test_real_operation_u_fault_restores_old_then_reinstalls(
    tmp_path: Path, operation: str, stage: str,
) -> None:
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)
    before = _installer_snapshot(root)
    guard = _InstallerGuard(operation=operation, stage=stage)
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert len(guard.receipts()) == 1
    assert _installer_snapshot(root) == before
    assert not list(root.glob(".happyranch-*"))
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not list(root.glob(".happyranch-*"))


def test_fresh_parent_mkdir_fault_cleans_only_owned_preparation(tmp_path: Path) -> None:
    """P0: a fault while creating the first fresh parent removes only owned dirs."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    (root / "unrelated.txt").write_bytes(b"keep")
    guard = _InstallerGuard(operation="parent:mkdir", stage="after")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(package, root, guard=guard)
    assert len(guard.receipts()) == 1
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    assert not (root / "opt").exists()
    assert not list(root.glob(".happyranch-*"))
    install_linux_package(package, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-one"


@pytest.mark.parametrize("operation", [
    "backup_remove:unlink",
    "backup_remove:rmdir",
    "marker_remove",
])
def test_committed_cleanup_fault_preserves_new_and_resumes(tmp_path: Path, operation: str) -> None:
    """C1-C3: committed cleanup faults keep NEW and remain retryable."""
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)
    guard = _InstallerGuard(operation=operation, stage="before", persistent=True)
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert not list(root.glob(".happyranch-*"))


def test_committed_cleanup_partial_backup_deletion_preserves_new(tmp_path: Path) -> None:
    """C1 interior deletion: a partial backup removal cannot roll back NEW."""
    root = _upgrade_root(tmp_path)
    _old, new = _old_new(tmp_path)
    guard = _InstallerGuard(operation="backup_remove:unlink", stage="after")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, guard=guard)
    assert guard.receipts()  # the injected fault really hit the cleanup seam
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    install_linux_package(new, root)
    assert not list(root.glob(".happyranch-*"))


def test_fresh_install_records_absence_and_rolls_back_to_absence(tmp_path: Path) -> None:
    """A/Fresh: prior absence is recorded, and rollback removes only owned paths."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    (root / "unrelated.txt").write_bytes(b"keep")
    guard = _InstallerGuard(operation="payload_publish", stage="after")
    with pytest.raises(OSError, match="injected"):
        install_linux_package(package, root, guard=guard)
    assert len(guard.receipts()) == 1
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    assert not (root / "opt").exists()
    assert not (root / "etc").exists()
    assert not list(root.glob(".happyranch-*"))
    install_linux_package(package, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-one"


def test_system_service_partial_dropin_write_restores_prior_dropin(tmp_path: Path) -> None:
    """I4: a torn drop-in write restores prior bytes/mode/directory/sibling."""
    root = _upgrade_root(tmp_path, system_service=False)
    _stage_system_credentials(root, enrollment=True)
    dropin_dir = root / "etc/systemd/system/happyranch-tsnet-sidecar.service.d"
    dropin_dir.mkdir(mode=0o710)
    dropin = dropin_dir / "10-enrollment-credential.conf"
    dropin.write_bytes(b"operator-managed-prior-dropin\n")
    dropin.chmod(0o640)
    sibling = dropin_dir / "99-other.conf"
    sibling.write_bytes(b"foreign-sibling\n")
    sibling.chmod(0o600)
    _old, new = _old_new(tmp_path)
    before = _installer_snapshot(root)
    guard = _InstallerGuard(operation="dropin_publish", stage="before", partial=True)
    with pytest.raises(OSError, match="injected"):
        install_linux_package(new, root, system_service=True, guard=guard)
    assert len(guard.receipts()) == 1
    assert _installer_snapshot(root) == before
    assert sibling.read_bytes() == b"foreign-sibling\n"
    assert not list(root.glob(".happyranch-*"))


# ---------------------------------------------------------------------------
# F. Loss-detecting fixtures and complete oracles (TASK8468 finding 3)
# ---------------------------------------------------------------------------

def _loss_detecting_packages(tmp_path: Path) -> tuple[Path, Path]:
    """OLD/NEW packages whose sidecar, connector AND wheel bytes all differ."""
    packages: list[Path] = []
    for version in ("1-OLD", "2-NEW"):
        folder = tmp_path / f"inputs-{version}"
        folder.mkdir()
        sidecar, connector, wheel, inventory, notices = _inputs(folder)
        label = version.encode()
        sidecar.write_bytes(b"sidecar-" + label)
        connector.write_bytes(b"connector-" + label)
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("runtime/version.py", f"VERSION = {version!r}\n")
        packages.append(
            build_linux_package(
                folder / "pkg.tar", sidecar, connector, wheel, inventory, notices,
                version=version,
            )
        )
    old, new = packages
    assert old != new
    return old, new


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


def _full_snapshot(root: Path) -> dict[str, tuple]:
    """Complete lstat oracle: type, bytes, file/dir modes, links, uid/gid."""
    state = {".": _node(root)}
    for path in sorted(root.rglob("*")):
        state[str(path.relative_to(root))] = _node(path)
    return state


def _rewrite_record(root: Path, mutate) -> dict:
    marker = root / TRANSACTION_MARKER
    record = json.loads(marker.read_text(encoding="utf-8"))
    mutate(record)
    marker.write_text(json.dumps(record), encoding="utf-8")
    marker.chmod(0o600)
    return record


def _preparing_root(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """A real preparing record: stage allocated, no prior OLD byte mutated."""
    package = _distinct_package(tmp_path, name, b"new")
    root = tmp_path / name / "root"
    root.mkdir(parents=True)
    guard = _InstallerGuard(
        operation="stage_payload:bin/happyranch-connector",
        stage="before",
        exception=_Interrupted,
    )
    with pytest.raises(_Interrupted):
        install_linux_package(package, root, guard=guard)
    assert (root / TRANSACTION_MARKER).exists()
    assert json.loads((root / TRANSACTION_MARKER).read_text())["phase"] == "preparing"
    return root, package


def test_loss_detecting_fixture_distinguishes_every_payload_member(tmp_path: Path) -> None:
    old, new = _loss_detecting_packages(tmp_path)
    old_root = tmp_path / "old-root"
    new_root = tmp_path / "new-root"
    install_linux_package(old, old_root)
    install_linux_package(new, new_root)
    for relative in (
        "bin/happyranch-tsnet-sidecar",
        "bin/happyranch-connector",
        "share/happyranch.whl",
        "manifest.json",
    ):
        assert (
            old_root / "opt/happyranch" / relative
        ).read_bytes() != (new_root / "opt/happyranch" / relative).read_bytes(), relative


# ---------------------------------------------------------------------------
# G. Fresh partial rollback resumes for every owned removal (finding 1, R1/R4)
# ---------------------------------------------------------------------------

_PAYLOAD_REMOVAL_OCCURRENCES = tuple(range(1, 11))


class _RollbackRemovalProbe:
    """Interrupt a real fresh-payload rollback after the Nth owned removal.

    The wrapper executes the saved ``os.unlink``/``os.rmdir`` binding first, so
    every receipt records a real mutation, and raises once the selected
    occurrence (or every occurrence when ``persistent``) is reached.
    """

    def __init__(self, root: Path, occurrence: int, exception: type[BaseException],
                 persistent: bool = False) -> None:
        self.opt = root / "opt/happyranch"
        self.occurrence = occurrence
        self.exception = exception
        self.persistent = persistent
        self.hits: list[tuple[str, str]] = []
        self._unlink = None
        self._rmdir = None

    def __enter__(self) -> "_RollbackRemovalProbe":
        self._unlink, self._rmdir = os.unlink, os.rmdir
        os.unlink = self._wrap(self._unlink, "unlink")
        os.rmdir = self._wrap(self._rmdir, "rmdir")
        return self

    def _wrap(self, original, operation: str):
        def wrapper(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            candidate = Path(path)
            if candidate == self.opt or self.opt in candidate.parents:
                self.hits.append((operation, str(candidate)))
                if self.persistent or len(self.hits) == self.occurrence:
                    raise self.exception("rollback-removal")
            return result

        return wrapper

    def __exit__(self, *_exc) -> bool:
        os.unlink, os.rmdir = self._unlink, self._rmdir
        return False


def _assert_fresh_absence(root: Path) -> None:
    assert not (root / "opt").exists()
    assert not (root / "etc").exists()
    assert not list(root.glob(".happyranch-*"))
    assert (root / "unrelated.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("exception", [OSError, KeyboardInterrupt], ids=["E", "K"])
@pytest.mark.parametrize("occurrence", _PAYLOAD_REMOVAL_OCCURRENCES)
def test_fresh_exception_rollback_resumes_at_every_payload_removal(
    tmp_path: Path, occurrence: int, exception: type[BaseException],
) -> None:
    """RB: an interrupted fresh rollback removal must resume to absence."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    (root / "unrelated.txt").write_bytes(b"keep")
    reference = tmp_path / "reference"
    install_linux_package(package, reference)
    expected = _inventory_tree(reference / "opt/happyranch")

    publication = _InstallerGuard(operation="payload_publish", stage="after")
    with pytest.raises((OSError, KeyboardInterrupt)):
        with _RollbackRemovalProbe(root, occurrence, exception) as probe:
            install_linux_package(package, root, guard=publication)
    assert len(probe.hits) == occurrence
    assert all(hit[1].startswith(str(root / "opt/happyranch")) for hit in probe.hits)
    # While faulted: fresh OLD absence preserved, partial NEW still owned.
    assert (root / TRANSACTION_MARKER).exists()
    assert not (root / _PAYLOAD_BACKUP_NAME).exists()
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    # Fault cleared: expose full fresh absence before any reinstall.
    _recover_interrupted(root)
    _assert_fresh_absence(root)
    install_linux_package(package, root)
    install_linux_package(package, root)
    assert _inventory_tree(root / "opt/happyranch") == expected
    assert not list(root.glob(".happyranch-*"))


@pytest.mark.parametrize("exception", [OSError, KeyboardInterrupt], ids=["E", "K"])
@pytest.mark.parametrize("occurrence", _PAYLOAD_REMOVAL_OCCURRENCES)
def test_fresh_interrupted_recovery_resumes_at_every_payload_removal(
    tmp_path: Path, occurrence: int, exception: type[BaseException],
) -> None:
    """REC: an interrupted fresh recovery removal must resume to absence."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    (root / "unrelated.txt").write_bytes(b"keep")
    reference = tmp_path / "reference"
    install_linux_package(package, reference)
    expected = _inventory_tree(reference / "opt/happyranch")

    publication = _InstallerGuard(
        operation="payload_publish", stage="after", exception=_Interrupted
    )
    with pytest.raises(_Interrupted):
        install_linux_package(package, root, guard=publication)
    assert (root / TRANSACTION_MARKER).exists()
    with pytest.raises((OSError, KeyboardInterrupt)):
        with _RollbackRemovalProbe(root, occurrence, exception) as probe:
            _recover_interrupted(root)
    assert len(probe.hits) == occurrence
    assert all(hit[1].startswith(str(root / "opt/happyranch")) for hit in probe.hits)
    assert not (root / _PAYLOAD_BACKUP_NAME).exists()
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    _recover_interrupted(root)
    _assert_fresh_absence(root)
    install_linux_package(package, root)
    install_linux_package(package, root)
    assert _inventory_tree(root / "opt/happyranch") == expected
    assert not list(root.glob(".happyranch-*"))


@pytest.mark.parametrize("route", ["RB", "REC"])
@pytest.mark.parametrize("exception", [OSError, KeyboardInterrupt], ids=["E", "K"])
def test_fresh_rollback_persistent_removal_fault_refuses_then_recovers(
    tmp_path: Path, route: str, exception: type[BaseException],
) -> None:
    """A persistent removal fault may refuse while present, never permanently."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    (root / "unrelated.txt").write_bytes(b"keep")
    reference = tmp_path / "reference"
    install_linux_package(package, reference)
    expected = _inventory_tree(reference / "opt/happyranch")

    if route == "RB":
        publication = _InstallerGuard(operation="payload_publish", stage="after")
        with pytest.raises((OSError, KeyboardInterrupt)):
            with _RollbackRemovalProbe(root, 1, exception, persistent=True):
                install_linux_package(package, root, guard=publication)
    else:
        publication = _InstallerGuard(
            operation="payload_publish", stage="after", exception=_Interrupted
        )
        with pytest.raises(_Interrupted):
            install_linux_package(package, root, guard=publication)
        with pytest.raises((OSError, KeyboardInterrupt)):
            with _RollbackRemovalProbe(root, 1, exception, persistent=True):
                _recover_interrupted(root)
    assert (root / TRANSACTION_MARKER).exists()
    assert not (root / _PAYLOAD_BACKUP_NAME).exists()
    assert (root / "unrelated.txt").read_bytes() == b"keep"
    # Fault cleared: coherent owned state must reach absence and reinstall.
    _recover_interrupted(root)
    _assert_fresh_absence(root)
    install_linux_package(package, root)
    install_linux_package(package, root)
    assert _inventory_tree(root / "opt/happyranch") == expected
    assert not list(root.glob(".happyranch-*"))


# ---------------------------------------------------------------------------
# H. Disjoint ownership negatives: two-call unchanged oracles (finding 2)
# ---------------------------------------------------------------------------

def test_foreign_stage_sentinel_attempt_identity_is_refused_unchanged(tmp_path: Path) -> None:
    """M4: a plausible attempt/stage name may not redirect to foreign content."""
    root, _package = _preparing_root(tmp_path, "foreign-stage")
    foreign = root / ".happyranch-stage-foreign-sentinel"
    foreign.mkdir(mode=0o750)
    sentinel = foreign / "sentinel"
    sentinel.write_bytes(b"FOREIGN")
    sentinel.chmod(0o640)
    _rewrite_record(root, lambda record: record.update(
        {"attempt_id": "foreign", "stage": str(foreign)}
    ))
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _full_snapshot(root) == before
    assert sentinel.read_bytes() == b"FOREIGN"
    assert stat.S_IMODE(foreign.lstat().st_mode) == 0o750


def test_arbitrary_created_parent_is_refused_unchanged(tmp_path: Path) -> None:
    """M4: created-parents is a finite plan, not an arbitrary delete list."""
    root, _package = _preparing_root(tmp_path, "foreign-parent")
    foreign = root / "foreign-empty"
    foreign.mkdir(mode=0o710)
    _rewrite_record(root, lambda record: record["created_parents"].append("foreign-empty"))
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _full_snapshot(root) == before
    assert foreign.is_dir()
    assert stat.S_IMODE(foreign.lstat().st_mode) == 0o710


def test_contradictory_committed_phase_is_refused_unchanged(tmp_path: Path) -> None:
    """M5: a preparing record relabelled committed has impossible facts."""
    root, _package = _preparing_root(tmp_path, "contradictory")
    _rewrite_record(root, lambda record: record.update({"phase": "committed"}))
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _full_snapshot(root) == before


def test_committed_record_with_unwritten_final_unit_is_refused_unchanged(
    tmp_path: Path,
) -> None:
    """F1: a real ``units_publishing`` record relabelled ``committed`` before the
    final unit was written must not delete the OLD backups.

    The intent list already names every unit, so the contradiction is only
    visible by comparing the complete active NEW composition (payload, units,
    drop-in) against the recorded intended identities.
    """
    old = _distinct_unit_package(tmp_path, "1", b"old")
    new = _distinct_unit_package(tmp_path, "2", b"new")
    root = tmp_path / "false-committed" / "root"
    install_linux_package(old, root)
    old_last_unit = (root / "etc/systemd/system" / UNITS[-1]).read_bytes()
    guard = _InstallerGuard(
        operation=f"unit_publish:{UNITS[-1]}", stage="before", occurrence=1,
        exception=_Interrupted,
    )
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=guard)
    assert guard.fired == 1
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    assert record["phase"] == "units_publishing"
    assert record["published_units"] == list(UNITS)
    _rewrite_record(root, lambda item: item.update({"phase": "committed"}))
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
        # Full lstat snapshot preserved after EACH refusal.
        assert _full_snapshot(root) == before
    # OLD backup authority and the mixed final unit are preserved intact.
    assert (root / _PAYLOAD_BACKUP_NAME).is_dir()
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-new"
    assert (root / "etc/systemd/system" / UNITS[-1]).read_bytes() == old_last_unit


def test_null_stage_identity_is_refused_and_owned_stage_preserved(
    tmp_path: Path,
) -> None:
    """F2: a real preparing record with a null stage must not abandon its stage.

    The writer always records the concrete allocated stage.  A null/missing
    identity is an unreachable/incomplete record: recovery must refuse before
    mutation and leave the owned stage and the full snapshot intact.
    """
    root, _package = _preparing_root(tmp_path, "null-stage")
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    stage = Path(record["stage"])
    assert stage.is_dir()
    _rewrite_record(root, lambda item: item.update({"stage": None}))
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
        assert _full_snapshot(root) == before
    # The real owned stage is neither removed nor abandoned.
    assert stage.is_dir()
    assert (root / TRANSACTION_MARKER).exists()


# ---------------------------------------------------------------------------
# F1/F2 entry-path coverage: the shipping ``install_linux_package`` reentry
# performs its own recovery, so every malformed/deceptive durable record must be
# refused there as well as through direct ``_recover_interrupted``.  Each call
# must return the same closed category and leave the complete lstat snapshot
# (including the OLD backups) unchanged after EACH of two refusals.
# ---------------------------------------------------------------------------


def _false_committed_root(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """A real ``units_publishing`` record relabelled ``committed``.

    The final unit write never happened, so the active installation is mixed
    while the intent list already names every unit.
    """
    old = _distinct_unit_package(tmp_path, "1", b"old")
    new = _distinct_unit_package(tmp_path, "2", b"new")
    root = tmp_path / name / "root"
    install_linux_package(old, root)
    guard = _InstallerGuard(
        operation=f"unit_publish:{UNITS[-1]}", stage="before", occurrence=1,
        exception=_Interrupted,
    )
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, guard=guard)
    assert guard.fired == 1
    assert json.loads((root / TRANSACTION_MARKER).read_text())["phase"] == "units_publishing"
    _rewrite_record(root, lambda item: item.update({"phase": "committed"}))
    return root, new


def _committed_partial_cleanup_root(
    tmp_path: Path, name: str, *, enrollment: bool,
) -> tuple[Path, Path]:
    """A genuine ``committed`` record with only the final cleanup step missing.

    Interrupting the real ``marker_remove`` leaves the durable committed record
    plus explicitly owned residue, so the active NEW composition can be mutated
    and validated exactly as the accepted committed oracle does.
    """
    root = _upgrade_root(tmp_path, system_service=True, enrollment=enrollment)
    _old, new = _old_new(tmp_path)
    guard = _InstallerGuard(
        operation="marker_remove", stage="before", exception=_Interrupted,
    )
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, system_service=True, guard=guard)
    assert guard.fired == 1
    assert json.loads((root / TRANSACTION_MARKER).read_text())["phase"] == "committed"
    return root, new


def test_install_reentry_refuses_contradictory_committed_and_preserves_old(
    tmp_path: Path,
) -> None:
    """F1 entry path: the shipping installer reentry refuses the impossible
    commit before its own recovery can delete the OLD backups."""
    root, new = _false_committed_root(tmp_path, "reentry-false-committed")
    assert (root / _PAYLOAD_BACKUP_NAME).is_dir()
    truth_backup = root / _UNIT_BACKUP_NAME
    old_last_unit = (truth_backup / UNITS[-1]).read_bytes()
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            install_linux_package(new, root)
        # Complete lstat snapshot, including the OLD backups, after EACH call.
        assert _full_snapshot(root) == before
    assert (root / _PAYLOAD_BACKUP_NAME).is_dir()
    assert truth_backup.is_dir()
    assert (truth_backup / UNITS[-1]).read_bytes() == old_last_unit
    assert (root / TRANSACTION_MARKER).exists()


def test_install_reentry_refuses_null_stage_and_preserves_stage(tmp_path: Path) -> None:
    """F2 entry path: a null stage must be refused before mutation by the
    shipping installer reentry, leaving the real owned stage intact."""
    root, package = _preparing_root(tmp_path, "reentry-null-stage")
    stage = Path(json.loads((root / TRANSACTION_MARKER).read_text())["stage"])
    assert stage.is_dir()
    _rewrite_record(root, lambda item: item.update({"stage": None}))
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            install_linux_package(package, root)
        assert _full_snapshot(root) == before
    assert stage.is_dir()
    assert (root / TRANSACTION_MARKER).exists()


def test_absent_stage_identity_is_refused_and_owned_stage_preserved(
    tmp_path: Path,
) -> None:
    """F2: an absent stage key is refused by the actual loader on both the
    direct-recovery and shipping-installer entry paths, twice each, with the
    owned stage and complete snapshot preserved after every call."""
    root, package = _preparing_root(tmp_path, "absent-stage")
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    stage = Path(record["stage"])
    assert stage.is_dir()
    _rewrite_record(root, lambda item: item.pop("stage"))
    before = _full_snapshot(root)
    for call in (
        lambda: _recover_interrupted(root),
        lambda: install_linux_package(package, root),
    ):
        for _ in range(2):
            with pytest.raises(PackageError, match="transaction_state_invalid"):
                call()
            assert _full_snapshot(root) == before
    assert stage.is_dir()
    assert (root / TRANSACTION_MARKER).exists()


_DIVERGENCE_CATEGORIES = ("bytes", "mode", "type", "absent")


def _genuine_committed_new_root(
    tmp_path: Path, name: str, *, enrollment: bool = False,
) -> tuple[Path, Path]:
    """A durable ``committed`` complete-NEW installation with owned residue.

    The real cleanup is interrupted before its first backup removal, so the
    authoritative committed record, the complete OLD payload/unit backup
    inventory and the owned stage all survive.  Unlike ``_false_committed_root``
    the active installation is genuinely complete NEW, so a divergence test
    introduces exactly one contradiction in one selected identity category
    instead of inheriting the retired fixture's unrelated OLD final unit.
    """
    root = _upgrade_root(tmp_path, system_service=True, enrollment=enrollment)
    _old, new = _old_new(tmp_path)
    guard = _InstallerGuard(
        operation="backup_remove:unlink", stage="before", occurrence=1,
        exception=_Interrupted,
    )
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, system_service=True, guard=guard)
    assert guard.fired == 1
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    assert record["phase"] == "committed"
    # The unmodified control is a genuine complete-NEW committed installation.
    _assert_committed_active_new(root, record)
    assert (root / _PAYLOAD_BACKUP_NAME).is_dir()
    assert (root / _UNIT_BACKUP_NAME).is_dir()
    return root, new


def _diverge(target: Path, category: str) -> None:
    """Change exactly one identity category of a real published artifact."""
    if category == "bytes":
        target.write_bytes(b"diverged-bytes\n")
    elif category == "mode":
        target.chmod(0o644)
    elif category == "type":
        target.unlink()
        target.symlink_to("diverged-type-target")
    elif category == "absent":
        target.unlink()
    else:
        raise AssertionError(f"unknown divergence category {category!r}")


def _assert_committed_divergence_refused(
    root: Path, new: Path, *, system_service: bool = True,
) -> None:
    """Refuse on both entry paths, twice each, with a complete snapshot oracle."""
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
        assert _full_snapshot(root) == before
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            install_linux_package(new, root, system_service=system_service)
        assert _full_snapshot(root) == before


@pytest.mark.parametrize("category", _DIVERGENCE_CATEGORIES)
def test_committed_active_new_payload_divergence_is_refused_unchanged(
    tmp_path: Path, category: str,
) -> None:
    """F1: a genuine committed complete-NEW payload is validated per category.

    Only the selected payload identity category diverges, so the refusal and
    full snapshot equality prove that category's own validation rather than an
    unrelated contradiction.
    """
    root, new = _genuine_committed_new_root(tmp_path, f"genuine-payload-{category}")
    _diverge(root / "opt/happyranch/bin/happyranch-tsnet-sidecar", category)
    _assert_committed_divergence_refused(root, new)


@pytest.mark.parametrize("unit", UNITS)
@pytest.mark.parametrize("category", _DIVERGENCE_CATEGORIES)
def test_committed_active_new_unit_divergence_is_refused_unchanged(
    tmp_path: Path, unit: str, category: str,
) -> None:
    """F1: every published unit of a genuine committed complete-NEW install is
    validated independently in bytes, mode, type and absence."""
    root, new = _genuine_committed_new_root(tmp_path, f"genuine-unit-{unit}-{category}")
    _diverge(root / "etc/systemd/system" / unit, category)
    _assert_committed_divergence_refused(root, new)


def test_genuine_committed_new_control_is_accepted_and_reinstalls(
    tmp_path: Path,
) -> None:
    """The equivalent control: unchanged genuine committed complete-NEW state is
    accepted, recovered, and then supports two successful installs."""
    root, new = _genuine_committed_new_root(tmp_path, "genuine-committed-control")
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    # The unchanged genuine committed complete-NEW state is accepted.
    _assert_committed_active_new(root, record)
    _recover_interrupted(root)
    assert not (root / TRANSACTION_MARKER).exists()
    assert not list(root.glob(".happyranch-*"))
    install_linux_package(new, root, system_service=True)
    install_linux_package(new, root, system_service=True)
    assert not (root / TRANSACTION_MARKER).exists()
    assert not list(root.glob(".happyranch-*"))


@pytest.mark.parametrize("category", _DIVERGENCE_CATEGORIES)
def test_committed_new_dropin_divergence_is_refused_unchanged(
    tmp_path: Path, category: str,
) -> None:
    """F1: the published NEW drop-in identity is part of complete active NEW and
    is validated per category from a genuine committed complete-NEW install."""
    root, new = _genuine_committed_new_root(
        tmp_path, f"genuine-dropin-new-{category}", enrollment=True,
    )
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    assert record["new_dropin"] is not None
    dropin = (
        root / "etc/systemd/system/happyranch-tsnet-sidecar.service.d"
        / "10-enrollment-credential.conf"
    )
    _diverge(dropin, category)
    _assert_committed_divergence_refused(root, new)


def _genuine_committed_prior_dropin_root(
    tmp_path: Path, name: str,
) -> tuple[Path, Path, Path]:
    """A genuine committed complete-NEW install that retained a prior drop-in.

    Interrupting the real cleanup before its first backup removal keeps the
    authoritative committed record, the complete OLD backup inventory and the
    operator-managed prior drop-in, whose recorded identity is the contract the
    unchanged-prior branch must validate.
    """
    root = _upgrade_root(tmp_path, system_service=True, enrollment=False)
    dropin_dir = root / "etc/systemd/system/happyranch-tsnet-sidecar.service.d"
    dropin_dir.mkdir(mode=0o710, exist_ok=True)
    dropin = dropin_dir / "10-enrollment-credential.conf"
    dropin.write_bytes(b"operator-managed-prior-dropin\n")
    dropin.chmod(0o640)
    _old, new = _old_new(tmp_path)
    guard = _InstallerGuard(
        operation="backup_remove:unlink", stage="before", occurrence=1,
        exception=_Interrupted,
    )
    with pytest.raises(_Interrupted):
        install_linux_package(new, root, system_service=True, guard=guard)
    assert guard.fired == 1
    record = json.loads((root / TRANSACTION_MARKER).read_text())
    assert record["phase"] == "committed"
    assert record["new_dropin"] is None
    assert record["dropin_present"] is True
    _assert_committed_active_new(root, record)
    return root, new, dropin


@pytest.mark.parametrize("category", _DIVERGENCE_CATEGORIES)
def test_committed_preserved_prior_dropin_divergence_is_refused_unchanged(
    tmp_path: Path, category: str,
) -> None:
    """F1: the unchanged-prior-drop-in branch is validated against its recorded
    prior identity in bytes, mode, type and absence, not inferred from the
    absence of a new drop-in."""
    root, new, dropin = _genuine_committed_prior_dropin_root(
        tmp_path, f"genuine-dropin-prior-{category}",
    )
    _diverge(dropin, category)
    _assert_committed_divergence_refused(root, new)


@pytest.mark.parametrize("field", ["created_parents", "published_units"])
def test_object_valued_record_elements_are_refused_not_typeerror(
    tmp_path: Path, field: str,
) -> None:
    """M3: element type is validated before any set/membership operation."""
    root, _package = _preparing_root(tmp_path, f"unhashable-{field}")
    _rewrite_record(root, lambda record: record.update({field: [{}]}))
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _full_snapshot(root) == before


def test_lone_record_temp_residue_is_preserved_and_refused(tmp_path: Path) -> None:
    """M6: an unrecorded transaction temp is unknown residue, never overwritten."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    root.mkdir()
    temporary = root / (TRANSACTION_MARKER + ".tmp")
    temporary.write_bytes(b"FOREIGN-TEMP")
    temporary.chmod(0o600)
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            _recover_interrupted(root)
    assert _full_snapshot(root) == before
    assert temporary.read_bytes() == b"FOREIGN-TEMP"


def test_root_ancestor_symlink_is_refused_and_external_target_unchanged(tmp_path: Path) -> None:
    """M8: no write may follow a symlink above the selected root."""
    package = _distinct_package(tmp_path, "1", b"one")
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    sentinel = external / "sentinel"
    sentinel.write_bytes(b"EXTERNAL")
    alias = tmp_path / "alias"
    alias.symlink_to(external, target_is_directory=True)
    root = alias / "chosen"
    before = _full_snapshot(external)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            install_linux_package(package, root)
    assert _full_snapshot(external) == before
    assert not (external / "chosen").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_wrong_leaf_type_is_refused_before_filesystem_operation(tmp_path: Path) -> None:
    """M8: an unexpected leaf type fails in the preflight category."""
    package = _distinct_package(tmp_path, "1", b"one")
    root = tmp_path / "root"
    units = root / "etc/systemd/system"
    units.mkdir(parents=True)
    fifo = units / UNITS[0]
    os.mkfifo(fifo, 0o600)
    before = _full_snapshot(root)
    for _ in range(2):
        with pytest.raises(PackageError, match="transaction_state_invalid"):
            install_linux_package(package, root)
    assert _full_snapshot(root) == before
    assert stat.S_ISFIFO(fifo.lstat().st_mode)


# ---------------------------------------------------------------------------
# I. Closed commit-authority and unit-restore cases, real bindings (finding 4)
# ---------------------------------------------------------------------------

def _upgrade_loss_detecting_root(tmp_path: Path) -> tuple[Path, Path]:
    """An OLD install with three distinguishable prior unit bodies/modes."""
    old, new = _loss_detecting_packages(tmp_path)
    root = tmp_path / "root"
    install_linux_package(old, root)
    for index, unit in enumerate(UNITS):
        destination = root / "etc/systemd/system" / unit
        destination.write_bytes(f"OLD-unit-{index}".encode())
        destination.chmod((0o600, 0o640, 0o644)[index])
    return root, new


@pytest.mark.parametrize("when", ["before", "after"])
def test_real_commit_replace_restores_old_or_preserves_new(tmp_path: Path, when: str) -> None:
    """I5: the real os.replace of the committed record owns the decision."""
    import tarfile

    root, new = _upgrade_loss_detecting_root(tmp_path)
    old = _full_snapshot(root)
    expected = tmp_path / "expected"
    shutil.copytree(root, expected, symlinks=True)
    install_linux_package(new, expected)
    # Bind the reference to the supplied NEW bytes and documented no-root modes
    # before comparing snapshots produced through the same installer.
    with tarfile.open(new) as archive:
        for member in archive.getmembers():
            relative = Path(member.name).relative_to("happyranch-linux-amd64")
            target = (
                expected / "etc/systemd/system" / relative.name
                if relative.parts[0] == "systemd"
                else expected / "opt/happyranch" / relative
            )
            assert target.read_bytes() == archive.extractfile(member).read()
            assert stat.S_IMODE(target.stat().st_mode) == (
                0o700 if relative.parts[0] == "bin" else 0o600
            )
    new_snapshot = _full_snapshot(expected)

    hits: list[dict] = []
    original = os.replace

    def replace(src, dst):
        is_commit = (
            Path(src) == _record_temp(root)
            and json.loads(Path(src).read_text(encoding="utf-8"))["phase"] == "committed"
        )
        if is_commit and not hits and when == "before":
            hits.append({"op": "os.replace", "when": when, "actual": False})
            raise OSError("commit-before")
        result = original(src, dst)
        if is_commit and not hits and when == "after":
            hits.append({"op": "os.replace", "when": when, "actual": True})
            raise OSError("commit-after")
        return result

    with unittest.mock.patch.object(os, "replace", replace):
        with pytest.raises(OSError, match="commit"):
            install_linux_package(new, root)
    assert len(hits) == 1
    assert hits[0]["actual"] is (when == "after")
    if when == "before":
        assert _full_snapshot(root) == old
        assert not list(root.glob(".happyranch-*"))
    else:
        assert _full_snapshot(root) == new_snapshot
        assert not (root / TRANSACTION_MARKER).exists()
        assert not list(root.glob(".happyranch-*"))
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert _full_snapshot(root) == new_snapshot


@pytest.mark.parametrize("route", ["RB", "REC"])
@pytest.mark.parametrize("unit", UNITS)
def test_each_unit_restore_resumes_in_both_routes(
    tmp_path: Path, route: str, unit: str,
) -> None:
    """R2: every unit restore interruption resumes to OLD, hit exactly once."""
    root, new = _upgrade_loss_detecting_root(tmp_path)
    old = _full_snapshot(root)
    hits: list[dict] = []
    original = os.replace

    def publication(name: str) -> None:
        if name == f"unit_published:{UNITS[-1]}":
            raise OSError("publication") if route == "RB" else _Interrupted("publication")

    backup = root / _UNIT_BACKUP_NAME
    backup_unit = backup / unit

    def restore(src, dst):
        result = original(src, dst)
        if not hits and Path(src) == backup_unit:
            hits.append({"source": str(Path(src).relative_to(root)), "actual": not Path(src).exists()})
            raise _Interrupted("restore-after")
        return result

    if route == "REC":
        with pytest.raises(_Interrupted):
            install_linux_package(new, root, fault=publication)
    with unittest.mock.patch.object(os, "replace", restore):
        with pytest.raises(_Interrupted):
            if route == "RB":
                install_linux_package(new, root, fault=publication)
            else:
                _recover_interrupted(root)
    assert len(hits) == 1
    assert hits[0]["source"] == f"{_UNIT_BACKUP_NAME}/{unit}"
    assert hits[0]["actual"] is True
    # Fault cleared: full OLD restoration, then two successful NEW installs.
    _recover_interrupted(root)
    assert _full_snapshot(root) == old
    install_linux_package(new, root)
    install_linux_package(new, root)
    assert (root / "opt/happyranch/bin/happyranch-tsnet-sidecar").read_bytes() == b"sidecar-2-NEW"
    assert not list(root.glob(".happyranch-*"))
