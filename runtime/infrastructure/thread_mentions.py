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

# NEW-message lexical contract only; historical release measurement freezes
# its old regex locally. Offsets never alter raw body bytes. This deliberately
# supports simple quote containers/code, not a general Markdown AST.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*")
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n|$)")
_QUOTE_RE = re.compile(r" {0,3}> ?")
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_BOUNDARIES = frozenset("([{<>\"'\u2018\u201c,:;!?")


def _mention_space(char: str) -> bool:
    cp = ord(char)
    return (9 <= cp <= 13 or cp in (32, 133, 160, 5760, 8232, 8233, 8239, 8287, 12288)
            or 8192 <= cp <= 8202)


def _backslash_start(text: str, offset: int) -> int:
    start = offset
    while start > 0 and text[start - 1] == "\\":
        start -= 1
    return start


def _code_mask(text: str) -> bytearray:
    mask = bytearray(len(text))
    region_start: int | None = None
    region_end = 0
    region_depth = 0
    fence: tuple[int, str, int] | None = None

    def finish_region() -> None:
        nonlocal region_start
        if region_start is None:
            return
        # Every maximal run can close established code, even after a slash.
        runs = [(m.start(), m.end()) for m in re.compile(r"`+").finditer(text, region_start, region_end)]
        next_run: dict[int, int] = {}
        closers: dict[int, int] = {}
        for index in range(len(runs) - 1, -1, -1):
            start, end = runs[index]
            length = end - start
            if length in next_run:
                closers[index] = next_run[length]
            next_run[length] = index
        index = 0
        while index < len(runs):
            start, _end = runs[index]
            closer = closers.get(index)
            if (start - _backslash_start(text, start)) % 2 == 0 and closer is not None:
                end = runs[closer][1]
                mask[start:end] = b"\x01" * (end - start)
                index = closer + 1
            else:
                index += 1
        region_start = None

    for line in _LINE_RE.finditer(text):
        start, end = line.span()
        if start == end:
            continue
        content = line.group().rstrip("\r\n")
        depth = 0
        offset = 0
        while prefix := _QUOTE_RE.match(content, offset):
            depth += 1
            offset = prefix.end()
        residual = content[offset:]
        if fence is not None and fence[0] > 0 and depth != fence[0]:
            fence = None
        marker = _FENCE_RE.fullmatch(residual)
        if fence is not None:
            mask[start:end] = b"\x01" * (end - start)
            if (depth == fence[0] and marker is not None
                    and marker[1][0] == fence[1] and len(marker[1]) >= fence[2]
                    and not marker[2].strip(" \t")):
                fence = None
            continue
        if marker is not None and (marker[1][0] != "`" or "`" not in marker[2]):
            finish_region()
            fence = (depth, marker[1][0], len(marker[1]))
            mask[start:end] = b"\x01" * (end - start)
            continue
        if not residual.strip(" \t"):
            finish_region()
            continue
        if region_start is not None and region_depth != depth:
            finish_region()
        if region_start is None:
            region_start = start
            region_depth = depth
        region_end = end
    finish_region()
    return mask


def parse_mentions(body_markdown: str | None) -> list[str]:
    """Extract explicit prose addresses, exact-spelling dedup in input order.

    Email/word interiors, escaped tokens and supported code are literal.
    Ordinary quoted prose remains eligible. No owner/routing knowledge or I/O.
    """
    if not body_markdown:
        return []
    text = body_markdown
    mask = _code_mask(text)
    seen: set[str] = set()
    out: list[str] = []
    for offset, char in enumerate(text):
        if char != "@" or mask[offset]:
            continue
        boundary = _backslash_start(text, offset)
        if (offset - boundary) % 2 or (boundary > 0 and
                not (_mention_space(text[boundary - 1]) or text[boundary - 1] in _BOUNDARIES)):
            continue
        token = _TOKEN_RE.match(text, offset + 1)
        if token is not None and token[0] not in seen:
            seen.add(token[0])
            out.append(token[0])
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
