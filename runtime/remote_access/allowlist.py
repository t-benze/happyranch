"""Explicit allow-by-method+normalized-template allow-list (contract §6.4).

The remote surface is an explicit positive allow-list; unclassified methods
and paths are denied by default. Template segments are ``{name}`` placeholders.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AllowEntry:
    method: str
    path_template: str


def template_matches(path: str, template: str) -> bool:
    """True when ``path`` matches ``template`` segment-for-segment, treating
    ``{name}`` segments as wildcards. No prefix/contains over-matching."""
    path_segments = path.split("/")
    template_segments = template.split("/")
    if len(path_segments) != len(template_segments):
        return False
    for actual, expected in zip(path_segments, template_segments):
        if expected.startswith("{") and expected.endswith("}"):
            continue
        if actual != expected:
            return False
    return True


class AllowList:
    """An explicit method+template allow-list with deny-by-default semantics."""

    def __init__(self, entries: tuple[AllowEntry, ...]) -> None:
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            key = (entry.method, entry.path_template)
            if key in seen:
                raise ValueError(f"duplicate allow entry {entry.method} {entry.path_template}")
            seen.add(key)
        self.entries = tuple(entries)

    def __len__(self) -> int:
        return len(self.entries)

    def match(self, method: str, path: str) -> AllowEntry | None:
        """Return the matching entry, or None (deny by default)."""
        for entry in self.entries:
            if entry.method == method and template_matches(path, entry.path_template):
                return entry
        return None

    def match_any_method(self, path: str) -> bool:
        """True when the path matches some template under ANY method — used to
        distinguish method-denied from route-denied (contract §6.4)."""
        return any(template_matches(path, entry.path_template) for entry in self.entries)
