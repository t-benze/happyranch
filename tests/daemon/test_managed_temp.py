from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.daemon import managed_temp as mt


NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _create(tmp_path: Path):
    store = tmp_path / "owned"
    receipt = mt.create_job_temp_root(
        store, org_slug="acme", task_id="TASK-1", job_id="JOB-1", agent="dev",
    )
    return store, receipt


def _inode_count(root: Path) -> int:
    return 1 + sum(1 for _ in root.rglob("*"))


def test_unreceipted_existing_store_is_permanently_ineligible(tmp_path):
    store = tmp_path / "ambiguous"
    store.mkdir()
    (store / "abandoned-looking").mkdir()
    assert mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True) == []
    assert (store / "abandoned-looking").is_dir()


def test_quarantine_restore_and_expiry_are_exact_and_recoverable(tmp_path):
    store, receipt = _create(tmp_path)
    root = Path(receipt.root)
    for index in range(20):
        (root / f"inode-{index}").write_text("x")
    active_inodes = _inode_count(root)
    ledger = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True, now=NOW)
    expiry = mt.quarantine(ledger[0], store_root=store, inactive=lambda _: True, now=NOW)
    assert not root.exists()
    assert Path(expiry.quarantine_path).stat().st_ino == receipt.inode
    assert _inode_count(Path(expiry.quarantine_path)) == active_inodes
    assert mt.restore(expiry, store_root=store, now=NOW + timedelta(days=1)) == root
    assert root.stat().st_ino == receipt.inode
    expiry = mt.quarantine(
        mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)[0],
        store_root=store, inactive=lambda _: True, now=NOW,
    )
    assert mt.expire(expiry, store_root=store, inactive=lambda _: True, now=NOW + timedelta(days=6)) is False
    assert mt.expire(expiry, store_root=store, inactive=lambda _: True, now=NOW + timedelta(days=8)) is True
    assert not Path(expiry.quarantine_path).exists()
    assert active_inodes == 22  # root + owner receipt + 20 controlled files


def test_candidate_becoming_active_is_rejected_at_action_time(tmp_path):
    store, _ = _create(tmp_path)
    ledger = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)
    with pytest.raises(ValueError, match="no longer eligible"):
        mt.quarantine(ledger[0], store_root=store, inactive=lambda _: False)


def test_path_inode_substitution_is_rejected(tmp_path):
    store, receipt = _create(tmp_path)
    ledger = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)
    root = Path(receipt.root)
    displaced = root.with_name("displaced")
    root.rename(displaced)
    root.mkdir()
    (root / mt._OWNER_FILE).write_text((displaced / mt._OWNER_FILE).read_text())
    with pytest.raises(ValueError, match="no longer eligible"):
        mt.quarantine(ledger[0], store_root=store, inactive=lambda _: True)
    assert displaced.is_dir()


def test_symlink_and_owner_receipt_mismatch_are_ineligible(tmp_path):
    store, receipt = _create(tmp_path)
    root = Path(receipt.root)
    (root / "escape").symlink_to(tmp_path)
    assert mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True) == []
    (root / "escape").unlink()
    owner = root / mt._OWNER_FILE
    payload = json.loads(owner.read_text())
    payload["uid"] += 1
    owner.write_text(json.dumps(payload))
    assert mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True) == []


def test_nested_mount_is_ineligible(tmp_path, monkeypatch):
    store, receipt = _create(tmp_path)
    nested = Path(receipt.root) / "nested"
    nested.mkdir()
    monkeypatch.setattr(mt.os.path, "ismount", lambda path: Path(path) == nested)
    assert mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True) == []


def test_protected_root_exclusion_is_exact(tmp_path):
    store, receipt = _create(tmp_path)
    assert mt.plan_quarantine(
        store, org_slug="acme", inactive=lambda _: True,
        protected_roots=(Path(receipt.root),),
    ) == []


def test_protected_root_is_revalidated_before_quarantine_and_expiry(tmp_path):
    store, receipt = _create(tmp_path)
    entry = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)[0]
    with pytest.raises(ValueError, match="no longer eligible"):
        mt.quarantine(
            entry, store_root=store, inactive=lambda _: True,
            protected_roots=(Path(receipt.root),), now=NOW,
        )
    expiry = mt.quarantine(
        entry, store_root=store, inactive=lambda _: True, now=NOW,
    )
    assert mt.plan_expiry(
        store, org_slug="acme", inactive=lambda _: True,
        protected_roots=(Path(expiry.quarantine_path),),
        now=NOW + timedelta(days=8),
    ) == []
    with pytest.raises(ValueError, match="no longer eligible"):
        mt.expire(
            expiry, store_root=store, inactive=lambda _: True,
            protected_roots=(Path(expiry.quarantine_path),),
            now=NOW + timedelta(days=8),
        )


def test_restore_rejects_expired_receipt(tmp_path):
    store, _ = _create(tmp_path)
    entry = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)[0]
    expiry = mt.quarantine(entry, store_root=store, inactive=lambda _: True, now=NOW)
    with pytest.raises(ValueError, match="has expired"):
        mt.restore(expiry, store_root=store, now=NOW + timedelta(days=8))


def test_quarantine_receipt_write_failure_leaves_inode_recoverable(tmp_path, monkeypatch):
    store, receipt = _create(tmp_path)
    entry = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)[0]
    real = mt._write_json_new

    def fail(path, payload):
        if path.name.endswith(".quarantined.json"):
            raise OSError("simulated interruption")
        return real(path, payload)
    monkeypatch.setattr(mt, "_write_json_new", fail)
    with pytest.raises(OSError, match="interruption"):
        mt.quarantine(entry, store_root=store, inactive=lambda _: True)
    assert (store / mt._QUARANTINE / receipt.receipt_id).stat().st_ino == receipt.inode
    # Restart recovery consumes the durable pre-mutation intent.
    recovered = mt.plan_expiry(
        store, org_slug="acme", inactive=lambda _: True,
        now=NOW + timedelta(days=8),
    )
    assert len(recovered) == 1
    assert recovered[0].receipt == receipt


def test_store_rejects_symlink_and_wrong_org(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        mt.create_job_temp_root(link, org_slug="acme", task_id="TASK-1", job_id="JOB-1", agent="dev")
    store, _ = _create(tmp_path)
    assert mt.plan_quarantine(store, org_slug="other", inactive=lambda _: True) == []


def test_quarantine_rejects_destination_parent_replaced_after_planning(tmp_path):
    store = tmp_path / "store"
    receipt = mt.create_job_temp_root(
        store, org_slug="acme", task_id="TASK-1", job_id="JOB-1", agent="dev",
    )
    entry = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)[0]
    original_parent = store / "quarantine.original"
    (store / "quarantine").rename(original_parent)
    escape = tmp_path / "escape"
    escape.mkdir()
    (store / "quarantine").symlink_to(escape, target_is_directory=True)

    with pytest.raises(ValueError, match="parent identity changed"):
        mt.quarantine(entry, store_root=store, inactive=lambda _: True)

    source = Path(receipt.root)
    assert source.stat().st_ino == receipt.inode
    assert not (escape / receipt.receipt_id).exists()
    assert not (original_parent / receipt.receipt_id).exists()


def test_restore_rejects_destination_parent_replaced_after_planning(tmp_path):
    store = tmp_path / "store"
    receipt = mt.create_job_temp_root(
        store, org_slug="acme", task_id="TASK-1", job_id="JOB-1", agent="dev",
    )
    entry = mt.plan_quarantine(store, org_slug="acme", inactive=lambda _: True)[0]
    expiry = mt.quarantine(entry, store_root=store, inactive=lambda _: True, now=NOW)
    original_parent = store / "active.original"
    (store / "active").rename(original_parent)
    escape = tmp_path / "escape"
    escape.mkdir()
    (store / "active").symlink_to(escape, target_is_directory=True)

    with pytest.raises(ValueError, match="parent identity changed"):
        mt.restore(expiry, store_root=store, now=NOW + timedelta(days=1))

    source = Path(expiry.quarantine_path)
    assert source.stat().st_ino == receipt.inode
    assert not (escape / receipt.receipt_id).exists()
    assert not (original_parent / receipt.receipt_id).exists()
