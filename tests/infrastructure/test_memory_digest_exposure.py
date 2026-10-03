"""Render-time exposure and the existing impression JSON boundary (THR-091)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.infrastructure.learnings_store import MemoryStore


HEADER = (
    "=== MEMORY-DIGEST (system) ===\n"
    "Relevant memory (pointers only — fetch bodies with `happyranch memory get <id>`):\n\n"
)
BODY = "Keep task credit scoped. Mention MEM-999 without exposing that item."
FIXTURE = (
    "---\nid: MEM-001\nslug: bounded-directive\ntitle: Bounded directive\ntopic: memory\n"
    "provenance: directive\nscope: agent\nlifecycle: valid\nsalience: 50\n---\n" + BODY
)
FULL = "**Directive:** `MEM-001` — Bounded directive  (directive, salience 60)\n" + BODY + "\n\n"
POINTER = "- `MEM-001` — Bounded directive  (directive, salience 60)\n"
NUDGE = 'Pull the long tail: `happyranch memory search "<terms>"`.\n'


@pytest.mark.parametrize(("budget", "expected", "pointers", "bodies", "byte_size"), [
    pytest.param(255, HEADER + FULL, (), ("MEM-001",), 259, id="R11b-full-body-char-boundary"),
    pytest.param(172, HEADER + POINTER, ("MEM-001",), (), 176, id="R11c-fallback-char-boundary"),
    pytest.param(254, HEADER + POINTER + NUDGE, ("MEM-001",), (), None, id="over-budget-body-falls-back-with-existing-nudge"),
    pytest.param(171, None, (), (), None, id="below-pointer-boundary"),
    pytest.param(114, None, (), (), None, id="header-only-is-none"),
    pytest.param(0, None, (), (), None, id="disabled"),
])
def test_directive_render_boundary(
    tmp_path: Path, budget: int, expected: str | None, pointers: tuple[str, ...],
    bodies: tuple[str, ...], byte_size: int | None,
) -> None:
    assert (len(FIXTURE), len(BODY), len(HEADER), len(FULL), len(POINTER)) == (216, 68, 114, 141, 58)
    root = tmp_path / "memory"
    root.mkdir()
    path = root / "MEM-001-bounded-directive.md"
    path.write_text(FIXTURE)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    store = MemoryStore(root)
    rendered = store.render_memory_digest("", budget=budget, scope="agent")
    assert rendered.text == expected
    assert rendered.pointer_ids == pointers
    assert rendered.full_body_ids == bodies
    assert rendered.digest_ids == bodies + pointers
    assert "MEM-999" not in rendered.digest_ids
    if byte_size is not None:
        assert len(rendered.text) == budget
        assert len(rendered.text.encode()) == byte_size
    # Compatibility wrapper has no second selector/renderer implementation.
    assert store.build_memory_digest("", budget=budget, scope="agent") == expected
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize(("case", "budget", "expected", "pointers", "bodies"), [
    ("mixed", 1000, HEADER + FULL + "- `MEM-002` — MEM-888 title mention  (experiential, salience 40)\n", ("MEM-002",), ("MEM-001",)),
    ("pointer-only", 1000, HEADER + "- `MEM-001` — Bounded directive  (directive, salience 50)\n", ("MEM-001",), ()),
    ("directive-omitted", 164, HEADER + "- `MEM-002` — Small  (experiential, salience 40)\n", ("MEM-002",), ()),
    ("nudge-only", 172, HEADER + NUDGE, (), ()),
    ("empty", 1500, None, (), ()),
])
def test_rendered_items_only(
    tmp_path: Path, case: str, budget: int, expected: str | None,
    pointers: tuple[str, ...], bodies: tuple[str, ...],
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    if case != "empty":
        fixture = FIXTURE
        if case == "pointer-only":
            fixture = fixture.replace("scope: agent", "scope: org")
        elif case == "directive-omitted":
            fixture = fixture.replace("Bounded directive", "L" * 150).replace("salience: 50", "salience: 0")
        elif case == "nudge-only":
            fixture = fixture.replace("provenance: directive", "provenance: experiential").replace("Bounded directive", "L" * 150)
        (root / "MEM-001-bounded-directive.md").write_text(fixture)
    if case in {"mixed", "directive-omitted", "nudge-only"}:
        title = "MEM-888 title mention" if case == "mixed" else ("Small" if case == "directive-omitted" else "Z" * 150)
        (root / "MEM-002-second.md").write_text(
            "---\nid: MEM-002\nslug: second\ntitle: " + title + "\ntopic: memory\n"
            "provenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 40\n---\nMEM-777 body mention"
        )
    rendered = MemoryStore(root).render_memory_digest("", budget=budget)
    assert rendered.text == expected
    assert rendered.pointer_ids == pointers
    assert rendered.full_body_ids == bodies
    assert rendered.digest_ids == bodies + pointers
    assert not {"MEM-777", "MEM-888", "MEM-999"}.intersection(rendered.digest_ids)


@pytest.mark.parametrize(("budget", "expected", "pointers", "bodies"), [
    (1000, HEADER + FULL + FULL, (), ("MEM-001",)),
    (313, HEADER + FULL + POINTER, (), ("MEM-001",)),
])
def test_duplicate_file_item_exposure_is_unique(
    tmp_path: Path, budget: int, expected: str, pointers: tuple[str, ...],
    bodies: tuple[str, ...],
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    for name in ("first", "second"):
        (root / f"MEM-001-{name}.md").write_text(FIXTURE)
    rendered = MemoryStore(root).render_memory_digest("", budget=budget)
    assert rendered.text == expected
    assert rendered.pointer_ids == pointers
    assert rendered.full_body_ids == bodies
    assert rendered.digest_ids == ("MEM-001",)


@pytest.mark.parametrize("overrides", [
    pytest.param({"memory_telemetry_version": None}, id="missing-version"),
    pytest.param({"memory_telemetry_version": 2}, id="wrong-version"),
    pytest.param({"memory_telemetry_version": True}, id="boolean-version"),
    pytest.param({"memory_telemetry_version": "1"}, id="string-version"),
    pytest.param({"pointer_ids": None}, id="missing-pointers"),
    pytest.param({"full_body_ids": None}, id="missing-bodies"),
    pytest.param({"pointer_ids": "MEM-001"}, id="scalar-pointers"),
    pytest.param({"full_body_ids": ("MEM-002",)}, id="tuple-bodies"),
    pytest.param({"digest_ids": "MEM-001"}, id="scalar-digest"),
    pytest.param({"pointer_ids": [1]}, id="non-string-id"),
    pytest.param({"pointer_ids": [[]]}, id="unhashable-id"),
    pytest.param({"pointer_ids": [""]}, id="empty-id"),
    pytest.param({"pointer_ids": ["private body text"]}, id="content-is-not-id"),
    pytest.param({"pointer_ids": ["MEM-1"]}, id="malformed-id"),
    pytest.param({"digest_ids": ["MEM-001", "MEM-001"]}, id="duplicate-digest"),
    pytest.param({"pointer_ids": ["MEM-001", "MEM-001"]}, id="duplicate-pointer"),
    pytest.param({"full_body_ids": ["MEM-002", "MEM-002"]}, id="duplicate-body"),
    pytest.param({"full_body_ids": ["MEM-001", "MEM-002"]}, id="overlap"),
    pytest.param({"pointer_ids": []}, id="missing-rendered-id-count"),
    pytest.param({"pointer_ids": ["MEM-001", "MEM-999"]}, id="extra-id-count"),
])
def test_invalid_exposure_leaves_real_database_unchanged(tmp_path: Path, overrides: dict[str, object]) -> None:
    db = Database(tmp_path / "test.db")
    try:
        logger = AuditLogger(db)
        logger.log_memory_read(agent="dev_agent", id="MEM-999", slug="existing")
        before = tuple(db._conn.iterdump())
        kwargs = dict(agent="dev_agent", task_id="TASK-001", session_id="sess-a",
                      digest_ids=["MEM-001", "MEM-002"], budget=1500,
                      memory_telemetry_version=1, pointer_ids=["MEM-001"], full_body_ids=["MEM-002"])
        kwargs.update(overrides)
        with pytest.raises(ValueError):
            logger.log_memory_digest_impression(**kwargs)
        assert tuple(db._conn.iterdump()) == before
    finally:
        db.close()


@pytest.mark.parametrize(("pointers", "bodies"), [
    (["MEM-001"], ["MEM-002"]),
    (["MEM-001", "MEM-002"], []),
    ([], ["MEM-001", "MEM-002"]),
    ([], []),
])
def test_observed_exposure_persisted_without_content(tmp_path: Path, pointers: list[str], bodies: list[str]) -> None:
    db = Database(tmp_path / "test.db")
    try:
        digest_ids = bodies + pointers
        AuditLogger(db).log_memory_digest_impression(
            agent="dev_agent", task_id="TASK-001", session_id="sess-a", digest_ids=digest_ids,
            budget=1500, memory_telemetry_version=1, pointer_ids=pointers, full_body_ids=bodies,
        )
        rows = db.fetch_all_readonly("SELECT * FROM audit_log")
        assert len(rows) == 1
        row = rows[0]
        assert (row["task_id"], row["agent"], row["action"]) == ("TASK-001", "dev_agent", "memory_digest_impression")
        assert json.loads(row["payload"]) == {
            "agent": "dev_agent", "session_id": "sess-a", "digest_ids": digest_ids,
            "digest_count": len(digest_ids), "budget": 1500, "memory_telemetry_version": 1,
            "pointer_ids": pointers, "full_body_ids": bodies,
        }
        # Observation version is never canary/epoch or eligibility authority.
        report = AuditLogger(db).compute_memory_telemetry_report(agent_role_map={"dev_agent": "worker"})
        assert report["decision"] == "insufficient_instrumentation"
        assert report["observation_period"]["thresholds_met"] is False
    finally:
        db.close()


@pytest.mark.parametrize("branch", ["final-item", "reserved-nudge", "line-only-last-fit", "directive-fit", "directive-fallback"])
def test_unidentifiable_pointer_budget_branches(tmp_path: Path, branch: str) -> None:
    """All appended invalid item forms preserve bytes and valid-only exposure."""
    root = tmp_path / "memory"
    root.mkdir()
    good_full = "**Directive:** `MEM-001` — Valid  (directive, salience 70)\nValid body.\n\n"
    (root / "MEM-001-valid.md").write_text(
        "---\nid: MEM-001\nslug: valid\ntitle: Valid\ntopic: memory\n"
        "provenance: directive\nscope: agent\nlifecycle: valid\nsalience: 60\n---\nValid body."
    )
    directive = branch.startswith("directive")
    provenance = "directive" if directive else "experiential"
    body = "Private body MEM-999. " * 30
    score = 60 if directive else 50
    bad_line = f"- `None` — A MEM-888  ({provenance}, salience {score})\n"
    bad_full = f"**Directive:** `None` — A MEM-888  (directive, salience 60)\n{body}\n\n"
    (root / "MEM-004-malformed.md").write_text(
        "---\nid: null\nslug: malformed\ntitle: A MEM-888\ntopic: memory\n"
        f"provenance: {provenance}\nscope: agent\nlifecycle: valid\nsalience: 50\n---\n{body}"
    )
    if branch in {"reserved-nudge", "line-only-last-fit"}:
        (root / "MEM-005-omitted.md").write_text(
            "---\nid: MEM-005\nslug: omitted\ntitle: " + "Z" * 300 + "\ntopic: memory\n"
            "provenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 40\n---\nOmitted body."
        )
    expected = HEADER + good_full + (bad_full if branch == "directive-fit" else bad_line)
    if branch in {"reserved-nudge", "directive-fallback"}:
        expected += NUDGE
    budget = len(expected)
    if branch == "line-only-last-fit":
        assert len(bad_line) < len(NUDGE)
    rendered = MemoryStore(root).render_memory_digest("", budget=budget)
    assert rendered.text == expected
    assert len(rendered.text) == budget
    assert rendered.full_body_ids == ("MEM-001",)
    assert rendered.pointer_ids == ()
    assert rendered.digest_ids == ("MEM-001",)
