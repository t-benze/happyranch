"""Tests for the pure org-portability root classifier (THR-187 Slice A).

Covers the exhaustive direct-child classification contract: every present root
is exactly one of include / named exclusion / reject, with work_hours proven in
both the present and absent fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from runtime.portability.roots import (
    EXCLUDE_CACHE,
    EXCLUDE_DERIVED_PROJECTION,
    EXCLUDE_GENERATED_MARKER,
    EXCLUDE_SQLITE_SIDECAR,
    EXCLUDE_TASK_OUTPUT,
    EXCLUDE_WORKSPACE_NON_MEMORY,
    EXCLUDE_ZERO_BYTE_LEGACY_RESIDUE,
    REJECT_INVALID_SKILL,
    REJECT_NONREGULAR,
    REJECT_NONZERO_LEGACY_RESIDUE,
    REJECT_UNKNOWN_ROOT,
    RootClassification,
    RootInventory,
    classify_root_entries,
)


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


_VALID_SKILL_YAML = (
    "description: QA scroll verification skill\n"
    "id: hr:qa-scroll-test\n"
    "name: QA Scroll Test Skill\n"
    "owner: operator\n"
    "policy_class: standard_operational\n"
    "slug: qa-scroll-test\n"
    "source: user_authored\n"
    "status: enabled\n"
    "version: 0.1.0\n"
    "when_to_use: ''\n"
)


def _build_full_org(root: Path, *, with_work_hours: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "happyranch.db").write_bytes(b"\x00\x01\x02")
    for d in ["org", "artifacts", "kb", "threads", "task-attachments", "jobs",
              "dreams", "schedules", "talks"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    if with_work_hours:
        (root / "work_hours").mkdir(parents=True, exist_ok=True)
    # valid legacy skill package
    _write(root / "skills" / "qa-scroll-test" / "skill.yaml", _VALID_SKILL_YAML)
    _write(root / "skills" / "qa-scroll-test" / "SKILL.md", "# QA Scroll Test\n\nbody\n")
    # workspaces: memory carve-out + non-memory data
    (root / "workspaces" / "dev_agent" / "memory").mkdir(parents=True, exist_ok=True)
    _write(root / "workspaces" / "dev_agent" / "memory" / "index.md", "# memory\n")
    _write(root / "workspaces" / "dev_agent" / "task_history.md", "history")
    (root / "workspaces" / "dev_agent" / "output").mkdir(parents=True, exist_ok=True)
    (root / "workspaces" / "dev_agent" / "repos").mkdir(parents=True, exist_ok=True)
    # generated markers / derived projection / sidecars / caches / zero-byte residue
    _write(root / ".hr_review_renamed")
    _write(root / ".org_settings_seeded")
    _write(root / "dashboard_projection.json", "{}")
    _write(root / "happyranch.db-wal", "wal")
    _write(root / "happyranch.db-shm", "shm")
    (root / ".pytest_cache").mkdir(parents=True, exist_ok=True)
    _write(root / ".DS_Store")
    _write(root / "audit.db")  # zero-byte legacy residue
    _write(root / "db.sqlite3")  # zero-byte legacy residue
    return root


def _paths(inventory: RootInventory, classification: RootClassification) -> set[str]:
    return {e.path for e in inventory.entries if e.classification == classification}


def test_full_fixture_no_rejections_and_exact_allowlist(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    inventory = classify_root_entries(root)

    assert not inventory.rejected, [e.model_dump() for e in inventory.rejected]

    included = _paths(inventory, RootClassification.INCLUDE)
    assert included == {
        "happyranch.db", "org", "artifacts", "kb", "threads", "task-attachments",
        "jobs", "dreams", "work_hours", "schedules", "talks",
        "skills/qa-scroll-test", "workspaces/dev_agent/memory",
    }

    # every workspace non-memory entry is a named exclusion
    assert any(
        e.path == "workspaces/dev_agent/task_history.md"
        and e.reason == EXCLUDE_WORKSPACE_NON_MEMORY
        for e in inventory.excluded
    )
    assert any(
        e.path == "workspaces/dev_agent/output"
        and e.reason == EXCLUDE_TASK_OUTPUT
        for e in inventory.excluded
    )
    assert any(
        e.path == "workspaces/dev_agent/repos"
        and e.reason == EXCLUDE_WORKSPACE_NON_MEMORY
        for e in inventory.excluded
    )

    # every marker/cache/sidecar/residue has its precise reason
    reasons = {e.path: e.reason for e in inventory.excluded}
    assert reasons[".hr_review_renamed"] == EXCLUDE_GENERATED_MARKER
    assert reasons[".org_settings_seeded"] == EXCLUDE_GENERATED_MARKER
    assert reasons["dashboard_projection.json"] == EXCLUDE_DERIVED_PROJECTION
    assert reasons["happyranch.db-wal"] == EXCLUDE_SQLITE_SIDECAR
    assert reasons["happyranch.db-shm"] == EXCLUDE_SQLITE_SIDECAR
    assert reasons[".pytest_cache"] == EXCLUDE_CACHE
    assert reasons[".DS_Store"] == EXCLUDE_CACHE
    assert reasons["audit.db"] == EXCLUDE_ZERO_BYTE_LEGACY_RESIDUE
    assert reasons["db.sqlite3"] == EXCLUDE_ZERO_BYTE_LEGACY_RESIDUE


def test_work_hours_absent_has_no_entry(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org", with_work_hours=False)
    inventory = classify_root_entries(root)
    assert not any(e.path == "work_hours" for e in inventory.entries)
    assert "work_hours" not in _paths(inventory, RootClassification.INCLUDE)
    # nothing is silently misclassified — every present child is classified
    assert not inventory.rejected


def test_unknown_root_rejected(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    inventory = classify_root_entries(root)
    assert any(
        e.path == "scripts" and e.reason == REJECT_UNKNOWN_ROOT
        for e in inventory.rejected
    )


def test_symlink_rejected_as_nonregular(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    (root / "evil").symlink_to(root / "org")
    inventory = classify_root_entries(root)
    assert any(
        e.path == "evil" and e.reason == REJECT_NONREGULAR
        for e in inventory.rejected
    )


def test_nonzero_legacy_residue_rejected(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    _write(root / "audit.db", "some real data")
    inventory = classify_root_entries(root)
    assert any(
        e.path == "audit.db" and e.reason == REJECT_NONZERO_LEGACY_RESIDUE
        for e in inventory.rejected
    )


def test_invalid_legacy_skill_rejected(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    # slug mismatch → invalid
    _write(
        root / "skills" / "broken" / "skill.yaml",
        _VALID_SKILL_YAML.replace("slug: qa-scroll-test", "slug: other-slug"),
    )
    _write(root / "skills" / "broken" / "SKILL.md", "# Broken\n")
    inventory = classify_root_entries(root)
    assert any(
        e.path == "skills/broken" and e.reason == REJECT_INVALID_SKILL
        for e in inventory.rejected
    )


def test_skill_yaml_symlink_rejected(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    external = tmp_path / "external-skill.yaml"
    _write(external, _VALID_SKILL_YAML)
    pkg = root / "skills" / "qa-scroll-test"
    pkg.joinpath("skill.yaml").unlink()
    pkg.joinpath("skill.yaml").symlink_to(external)
    inventory = classify_root_entries(root)
    assert any(
        e.path == "skills/qa-scroll-test" and e.reason == REJECT_INVALID_SKILL
        for e in inventory.rejected
    )


def test_skill_md_symlink_rejected(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    external = tmp_path / "external-SKILL.md"
    _write(external, "# External Skill\n\nbody\n")
    pkg = root / "skills" / "qa-scroll-test"
    pkg.joinpath("SKILL.md").unlink()
    pkg.joinpath("SKILL.md").symlink_to(external)
    inventory = classify_root_entries(root)
    assert any(
        e.path == "skills/qa-scroll-test" and e.reason == REJECT_INVALID_SKILL
        for e in inventory.rejected
    )


@pytest.mark.parametrize("member", ["references", "assets"])
def test_optional_dir_dangling_symlink_rejected(tmp_path: Path, member: str) -> None:
    root = _build_full_org(tmp_path / "org")
    pkg = root / "skills" / "qa-scroll-test"
    # Dangling symlink: the target does not exist, so Path.exists() is False
    # and the pre-fix validator treats it as absent (accepted). Must reject.
    pkg.joinpath(member).symlink_to(tmp_path / "does-not-exist")
    inventory = classify_root_entries(root)
    assert any(
        e.path == "skills/qa-scroll-test" and e.reason == REJECT_INVALID_SKILL
        for e in inventory.rejected
    )


def test_optional_dirs_regular_files_accepted(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    pkg = root / "skills" / "qa-scroll-test"
    _write(pkg / "references" / "guide.md", "# Guide\n")
    _write(pkg / "assets" / "icon.svg", "<svg/>\n")
    inventory = classify_root_entries(root)
    assert not inventory.rejected, [e.model_dump() for e in inventory.rejected]
    assert "skills/qa-scroll-test" in _paths(inventory, RootClassification.INCLUDE)


def test_system_contract_skill_rejected(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    _write(
        root / "skills" / "sys-skill" / "skill.yaml",
        _VALID_SKILL_YAML.replace("slug: qa-scroll-test", "slug: sys-skill")
        .replace("id: hr:qa-scroll-test", "id: hr:sys-skill")
        .replace("policy_class: standard_operational", "policy_class: system_contract"),
    )
    _write(root / "skills" / "sys-skill" / "SKILL.md", "# Sys\n")
    inventory = classify_root_entries(root)
    assert any(
        e.path == "skills/sys-skill" and e.reason == REJECT_INVALID_SKILL
        for e in inventory.rejected
    )


def test_non_memory_workspace_symlink_rejected(tmp_path: Path) -> None:
    root = _build_full_org(tmp_path / "org")
    (root / "workspaces" / "dev_agent" / "leak").symlink_to(root / "org")
    inventory = classify_root_entries(root)
    assert any(
        e.path == "workspaces/dev_agent/leak" and e.reason == REJECT_NONREGULAR
        for e in inventory.rejected
    )


def test_org_root_must_be_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("x")
    with pytest.raises(ValueError):
        classify_root_entries(not_a_dir)


@pytest.mark.parametrize(
    "md_body",
    [
        "# QA\n\n[leak](file:references/guide.md)\n",
        "# QA\n\n[leak](/etc/passwd)\n",
        "# QA\n\n[leak](../other/secret.md)\n",
        "# QA\n\n[leak](../../org/teams.yaml)\n",
    ],
    ids=["file-uri", "absolute", "dotdot", "cross-package"],
)
def test_unsafe_legacy_reference_rejected_at_classification(
    tmp_path: Path, md_body: str,
) -> None:
    """An unsafe local reference (file:/absolute/../) makes the legacy skill
    package REJECT at classification time (preflight refuses the source org)."""
    root = _build_full_org(tmp_path / "org")
    pkg = root / "skills" / "qa-scroll-test"
    _write(pkg / "SKILL.md", md_body)
    _write(pkg / "references" / "guide.md", "# Guide\n")
    inventory = classify_root_entries(root)
    assert any(
        e.path == "skills/qa-scroll-test" and e.reason == REJECT_INVALID_SKILL
        for e in inventory.rejected
    )


def test_remote_reference_is_inert_at_classification(tmp_path: Path) -> None:
    """A remote URL reference does not reject a valid legacy skill package."""
    root = _build_full_org(tmp_path / "org")
    pkg = root / "skills" / "qa-scroll-test"
    _write(pkg / "SKILL.md", "# QA\n\nSee [remote](https://example.com/x).\n")
    inventory = classify_root_entries(root)
    assert not any(
        e.path == "skills/qa-scroll-test" for e in inventory.rejected
    )
    assert "skills/qa-scroll-test" in _paths(inventory, RootClassification.INCLUDE)
