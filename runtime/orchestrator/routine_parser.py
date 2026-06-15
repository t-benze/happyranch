"""Parser for the ``## Routine Tasks`` section of an agent file.

A working-hours wake reads the waking agent's routine checklist and turns each
top-level list item into one self-dispatched root task. This module performs
that parse (used by the daemon both to gate scheduling and to inject the
section verbatim into the wake prompt). It is pure and string-in/result-out so
it can be unit tested in isolation, mirroring ``dream_runner``'s prompt tests.

Contract (see ``2026-06-10-working-hours-design.md`` "Routine Tasks"):

- Locate the first H2 whose text is exactly ``## Routine Tasks``. The section
  body runs to the next H2 (``## ``) or EOF.
- Routines are the top-level list items (``- `` / ``* `` / ``1. ``) in the
  body, each carrying its nested continuation lines.
- Non-list prose before the first list item is a preamble: shared context,
  not a task.
- An absent section, or a section with zero list items, means "no routines" —
  the scheduler skips silently (no row, no wake).
- ``MAX_ROUTINES_PER_WAKE`` bounds how many tasks one wake may spawn; routines
  beyond the cap are dropped and the count is RECORDED (no silent truncation).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ROUTINES_PER_WAKE = 20

_HEADER_RE = re.compile(r"^##[ \t]+Routine Tasks[ \t]*$")
_H2_RE = re.compile(r"^##[ \t]")
_LIST_ITEM_RE = re.compile(r"^([-*]|\d+\.)[ \t]+\S")


@dataclass(frozen=True)
class RoutineParseResult:
    present: bool           # the ``## Routine Tasks`` header was found
    preamble: str           # prose before the first list item (may be empty)
    routines: list[str]     # kept routines (<= MAX_ROUTINES_PER_WAKE), verbatim
    dropped: int            # routines discarded beyond the cap

    @property
    def has_wake(self) -> bool:
        """True iff the section is present AND has at least one routine — the
        precondition for scheduling a wake."""
        return self.present and len(self.routines) > 0


def parse_routines(
    markdown: str, *, max_routines: int = MAX_ROUTINES_PER_WAKE,
) -> RoutineParseResult:
    lines = markdown.splitlines()

    header_idx = next((i for i, line in enumerate(lines) if _HEADER_RE.match(line)), None)
    if header_idx is None:
        return RoutineParseResult(present=False, preamble="", routines=[], dropped=0)

    body: list[str] = []
    for line in lines[header_idx + 1:]:
        if _H2_RE.match(line):
            break
        body.append(line)

    preamble_lines: list[str] = []
    routines_raw: list[list[str]] = []
    current: list[str] | None = None
    seen_item = False

    for line in body:
        if _LIST_ITEM_RE.match(line):
            if current is not None:
                routines_raw.append(current)
            current = [line]
            seen_item = True
        elif line.strip() == "" or line[:1] in (" ", "\t"):
            # Blank line or indented continuation: part of the current routine,
            # or preamble whitespace before the first item.
            if current is not None:
                current.append(line)
            elif not seen_item:
                preamble_lines.append(line)
        else:
            # Non-indented prose. Closes any open routine; before the first
            # item it is preamble, between/after lists it is ignored context.
            if current is not None:
                routines_raw.append(current)
                current = None
            if not seen_item:
                preamble_lines.append(line)

    if current is not None:
        routines_raw.append(current)

    routines = [text for text in ("\n".join(r).rstrip() for r in routines_raw) if text.strip()]
    kept = routines[:max_routines]
    dropped = len(routines) - len(kept)
    preamble = "\n".join(preamble_lines).strip()

    return RoutineParseResult(
        present=True, preamble=preamble, routines=kept, dropped=dropped,
    )
