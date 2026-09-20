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
from pathlib import Path

import pytest

from runtime.remote_access.linux_package import (
    PackageError,
    TRANSACTION_MARKER,
    UNITS,
    _PAYLOAD_BACKUP_NAME,
    _UNIT_BACKUP_NAME,
    _recover_interrupted,
    install_linux_package,
)
from tests.remote_access.test_linux_package import (
    _InstallerGuard,
    _distinct_package,
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
