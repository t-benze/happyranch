from __future__ import annotations

from runtime.orchestrator.routine_parser import (
    MAX_ROUTINES_PER_WAKE,
    parse_routines,
)


def test_absent_section_is_no_wake() -> None:
    result = parse_routines("# Agent\n\nSome role description.\n")
    assert result.present is False
    assert result.has_wake is False
    assert result.routines == []


def test_empty_section_is_no_wake() -> None:
    md = "## Routine Tasks\n\nJust context, no checklist items here.\n"
    result = parse_routines(md)
    assert result.present is True
    assert result.has_wake is False
    assert result.routines == []
    assert "Just context" in result.preamble


def test_basic_list_extraction_dash_and_numbered() -> None:
    md = """## Routine Tasks

- Triage open customer tickets.
- Send follow-ups for tickets awaiting reply > 24h.
"""
    result = parse_routines(md)
    assert result.has_wake is True
    assert result.routines == [
        "- Triage open customer tickets.",
        "- Send follow-ups for tickets awaiting reply > 24h.",
    ]
    assert result.dropped == 0

    numbered = parse_routines("## Routine Tasks\n\n1. First routine.\n2. Second routine.\n")
    assert [r for r in numbered.routines] == ["1. First routine.", "2. Second routine."]


def test_preamble_before_first_item_is_separate() -> None:
    md = """## Routine Tasks

Run these every wake; phrase briefs relative to the last wake.

- Check the deploy queue.
"""
    result = parse_routines(md)
    assert result.preamble == "Run these every wake; phrase briefs relative to the last wake."
    assert result.routines == ["- Check the deploy queue."]


def test_nested_continuation_lines_stay_with_item() -> None:
    md = """## Routine Tasks

- Triage tickets
  - escalate billing disputes
  continuation prose for the same routine
- Second routine
"""
    result = parse_routines(md)
    assert len(result.routines) == 2
    assert "escalate billing disputes" in result.routines[0]
    assert "continuation prose" in result.routines[0]
    assert result.routines[1] == "- Second routine"


def test_section_ends_at_next_h2() -> None:
    md = """## Routine Tasks

- Real routine one.

## Other Section

- This is NOT a routine.
"""
    result = parse_routines(md)
    assert result.routines == ["- Real routine one."]


def test_cap_records_drop_without_silent_truncation() -> None:
    items = "\n".join(f"- routine {i}" for i in range(25))
    result = parse_routines(f"## Routine Tasks\n\n{items}\n")
    assert len(result.routines) == MAX_ROUTINES_PER_WAKE
    assert result.dropped == 5
    assert result.has_wake is True


def test_h3_subheader_does_not_end_section() -> None:
    md = """## Routine Tasks

- One

### A sub-note

- Two
"""
    result = parse_routines(md)
    assert result.routines == ["- One", "- Two"]
