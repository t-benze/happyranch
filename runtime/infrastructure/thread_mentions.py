"""Thread mention grammar and pure wake resolution, plus write admission.

The parser and wake helpers perform no I/O. Live writers use the admission
wrapper to obtain an org-local current-message classification outside the
Database RLock; transactions validate it before their first effect.

Ratified first-release contract (THR-198 seq 108-110):

    resolve_wake_set(mentioned, participants, speaker):
      * valid mentions        -> exactly that valid set
      * zero valid mentions   -> participants - speaker (fallback)
        (including invalid/nonparticipant-only and self-only bodies)

Only live MESSAGE writers supply the ephemeral founder_only exception. Pure
historical measurement callers keep their original default behavior. Durable
mentions_json contains canonical eligible participant IDs, never label proof.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable


@dataclass(frozen=True)
class MessageAddresses:
    """Ephemeral admission of THIS message; never a historical receipt."""

    agents: tuple[str, ...]
    founder_only: bool = False
    naming_rows: tuple | None = None


def classify_message_write(method: Callable[..., Any]) -> Callable[..., Any]:
    """Prepare outside the synchronized DB method; never accept client proof.

    The org adapter owns filesystem capture. The wrapped transaction rechecks
    its SQL evidence before its first effect, without filesystem/DB inversion.
    These entry points are keyword-only; reply has implicit kind=MESSAGE.
    """
    @wraps(method)
    def admitted(self: Any, **kwargs: Any) -> Any:
        kind = kwargs.get('kind')
        kwargs['_addresses'] = (
            self.prepare_thread_message(kwargs.get('body_markdown'))
            if kind is None or kind.value == 'message' else None
        )
        return method(self, **kwargs)
    return admitted

# Agent tokens are @-prefixed names; the charset mirrors canonical agent
# names (letters, digits, underscore, hyphen; dots only between name parts,
# so sentence punctuation like "@dev_agent." is never swallowed). The
# Live naming classification precedes participant eligibility. The parser
# itself remains unchanged, including quoted/email-interior token matches.
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
    founder_only: bool = False,
) -> list[str]:
    """The ratified wake-set matrix. Pure — no I/O.

    ``participants`` is the live roster (typically ordered by the store's
    participant listing); the broadcast fallback preserves that order minus
    the speaker. Valid mentions route to exactly that set, in first-
    occurrence order. Mention routing is UNCONDITIONAL (founder ruling,
    TASK-6027): the persisted ``threads.mention_routing_enabled`` column is
    inert legacy and is never consulted to alter routing.
    """
    # Live writers supply this separately from empty persisted mentions.
    # Default false preserves every historical/pure measurement caller.
    if founder_only:
        return []
    broadcast = [name for name in participants if name != speaker]
    valid = valid_mentions(mentioned, participants, speaker)
    if valid:
        return valid
    return broadcast
