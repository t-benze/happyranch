"""Phase-2 thread mention routing (THR-198) — pure parse/resolve helpers.

Slice A ships this module as the single, pure, unit-testable source of
truth for mention extraction and the ratified wake-set matrix. It performs
NO I/O: callers supply the participant roster (the live thread roster at
write time) and the per-thread setting.

Ratified first-release contract (THR-198 seq 108-110):

    resolve_wake_set(mentioned, participants, speaker, enabled):
      * disabled                        -> participants - speaker (broadcast)
      * enabled + valid mentions        -> exactly that valid set
      * enabled + zero valid mentions   -> participants - speaker (fallback)
        (including invalid/nonparticipant-only and self-only bodies)

Slice A deliberately does NOT wire this into production wake routing —
the store persists the valid participant subset (``valid_mentions``) as
``thread_messages.mentions_json``; routing lands in Slice B.
"""

from __future__ import annotations

import re

# Agent tokens are @-prefixed names; the charset mirrors canonical agent
# names (letters, digits, underscore, hyphen; dots only between name parts,
# so sentence punctuation like "@dev_agent." is never swallowed). The
# participant-roster match is the canonical guard — tokens that are not live
# participant names (e.g. the "@founder" literal, typos, emails' domain
# halves) are simply invalid.
_MENTION_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)")


def parse_mentions(body_markdown: str | None) -> list[str]:
    """Extract @-tokens from a message body.

    Returns the raw canonical tokens in first-occurrence order, de-duplicated.
    Deterministic for a given body; no participant/speaker knowledge is
    applied here (see ``valid_mentions`` / ``resolve_wake_set``).
    """
    if not body_markdown:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _MENTION_RE.finditer(body_markdown):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def valid_mentions(
    mentioned: list[str],
    participants: list[str],
    speaker: str,
) -> list[str]:
    """Reduce parsed mentions to the canonical valid set: live participants
    at resolve time, excluding the speaker, de-duplicated with stable
    (first-occurrence) order.

    This is the durable signal persisted as ``mentions_json`` and the exact
    set the wake resolver routes to when non-empty.
    """
    roster = set(participants)
    seen: set[str] = set()
    out: list[str] = []
    for name in mentioned:
        if name == speaker or name not in roster or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def resolve_wake_set(
    mentioned: list[str],
    participants: list[str],
    speaker: str,
    *,
    mention_routing_enabled: bool,
) -> list[str]:
    """The ratified wake-set matrix. Pure — no I/O.

    ``participants`` is the live roster (typically ordered by the store's
    participant listing); the broadcast fallback preserves that order minus
    the speaker. Valid mentions route to exactly that set, in first-
    occurrence order.
    """
    broadcast = [name for name in participants if name != speaker]
    if not mention_routing_enabled:
        return broadcast
    valid = valid_mentions(mentioned, participants, speaker)
    if valid:
        return valid
    return broadcast
