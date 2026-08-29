"""THR-198 Phase-2 Slice D — read-only thread mention-routing release measurement.

This module is the founder-approved analytics/release-measurement remainder
for Phase-2 mention routing (THR-198 seq 108-110; measurement contract pinned
in ``docs/operations/gh-688-phase1-release-checklist.md`` §8 and
``output/TASK-5647/storage-gate-decision.md`` §8).

Design contract:

* READ-ONLY. Every query runs against a ``sqlite3`` connection opened with
  ``mode=ro``; no row is ever written. No deployment, restart, or production
  mutation is performed or authorized by this module.
* ISOLATED. Nothing in the daemon, CLI, or web imports this module. It is an
  operator-run measurement harness::

      uv run python -m runtime.infrastructure.thread_release_measurement \
          --db <org>/happyranch.db --epoch 2026-08-26T14:25:23Z --mode all

* STDLIB-ONLY. No new dependency. Reuses the pure resolver
  ``runtime/daemon/thread_mentions.py`` (parse_mentions / valid_mentions /
  resolve_wake_set) for the pre-change replay.
* DETERMINISTIC. All windows are half-open ``[start, end)`` over parsed UTC
  datetimes; ``as_of`` is explicit (defaults to now only at the CLI boundary,
  never inside the measurement functions). Every population that claims the
  observation instant is bounded by the half-open cutoff
  ``min(as_of, window_end)`` — an interim record never contains rows after
  its stated ``as_of``. A terminal decline outcome is observable at that
  instant only when its ``consumed_at`` (the schema's single terminal-time
  stamp) is strictly earlier than the cutoff: a wake enqueued before the
  cutoff but declined at or after it stays in the wake denominator, never
  the decline numerator, at the earlier observation instant.
* RANGE-AWARE COVERAGE. GH-688 Phase-1 coalescing lets ONE consumed REPLY
  cover an inclusive sequence range; coverage uses the authoritative
  immutable claimed range (the ``thread_reply_wake_claimed`` audit's
  ``from_seq..through_seq``, floor = the invocation's ``triggering_seq``),
  never an exact-``triggering_seq`` join.
* CREATING-ARRIVAL ATTRIBUTION (G1). A live mentioned wake is attributed to
  the conversational arrival that MINTED it — the ``thread_reply_wake_created``
  audit's ``through_seq`` (both production mint paths record it as the message
  seq being processed) — never to ``triggering_seq``. GH-688 Phase-1
  coalescing makes ``triggering_seq`` the retained range floor
  (``acknowledged + 1``), which can be an earlier message that never woke the
  agent: a later unmentioned broadcast wake would otherwise be falsely
  attributed to an earlier mention. A ``thread_reply_wake_coalesced`` arrival
  mints nothing and never re-attributes. A replacement minted by restart
  recovery has no created audit and is attributed to its
  ``thread_reply_wake_recovered`` (``replacement_queued``) audit's
  ``through_seq`` — the pair's required watermark at recovery, i.e. the
  actual wake-causing arrival (never the retained floor). Only genuinely
  unattributable rows (a follow-on minted at settlement, a legacy pre-audit
  row) fall back to ``triggering_seq`` — production-faithful for follow-ons
  (the retained range contains only arrivals that woke the agent).

  Every audit evidence lookup is scoped by the production ownership tuple
  — ``thread_id`` + ``agent_name`` (the wake owner recorded in
  ``audit_log.agent``) + the 8-char ``token_prefix`` — never a weaker key,
  so an unrelated same-thread/same-prefix audit owned by a DIFFERENT agent
  can never reattribute an invocation. All seq/range fields are parsed
  fail-closed: a missing, non-integer, boolean-like, float, string,
  non-positive, or internally inverted range payload is skipped without
  exception — the measurement never crashes and never fabricates an
  attribution.

Ratified gates (THR-198 seq 87/88/108):

* G1 mentioned-message saving (gate): baseline 293/499 = 58.7% August 2026;
  expectation ~24/204 ≈ 12% among retained wakes.
* G2 founder-message coverage (gate): baseline 698 founder messages that
  received replies; permitted losses 0.
* G3 org-wide decline rate (report only): expect ≈65%; not a gate.

Observed values are ALWAYS reported separately from the required criteria;
a partial window (``as_of < window_end``) is labelled interim and is never a
release result (THR-198 seq 129).
"""

from __future__ import annotations

import argparse
import calendar
import json
import sqlite3
import sys
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ratified constants (THR-198 seq 87/88/108; seq 128/129 for the epoch).
# ---------------------------------------------------------------------------

#: Phase-2 measurement epoch — the daemon restart that activated Slice C1,
#: the decline-doctrine fix, and the stdin fix (seq 128/129). NOT the
#: Slice-B merge timestamp.
RATIFIED_EPOCH = "2026-08-26T14:25:23Z"

#: TASK-5966 F1 rollout measurement boundary — the daemon restart that first
#: runs the strict mention-led exchange code. A SEPARATE boundary from the
#: Phase-2 epoch/window: the exchange changes the decline/wake populations
#: wholesale, so F1 metrics are never mixed into the Phase-2 G1/G2/G3
#: numbers. ``RATIFIED_EPOCH`` and the August 2026 baseline window are
#: preserved untouched. Write-time-frozen: messages written before the F1
#: epoch never open exchanges (no historical backfill).
RATIFIED_EPOCH_F1 = "2026-08-28T06:22:21Z"

#: F1 exchange gates (falsifiable, founder-ratified):
#:   * no-pierce: ``sum(wake_created)`` for held pairs inside open exchanges
#:     is ZERO (except the three documented pierce sources: mention, a
#:     pre-existing queued wake claimed mid-E, TASK_FOLLOWUP/BOOTSTRAP);
#:   * exactly-one catch-up: per deferred pair per exchange,
#:     ``count(queued tokens covering [open_seq, close_seq]) in {0, 1}``
#:     (0 only when a pierce/coalesce already covered it);
#:   * N-to-1: per founder-mention-led burst, per deferred pair, wake
#:     sessions drop from up to 5 (shipped) to exactly 1;
#:   * G2 coverage containment over the F1 window is at baseline.
REQUIRED_F1 = (
    "zero wakes for held pairs inside open exchanges except the three "
    "documented pierces; exactly one range-covering catch-up per deferred "
    "pair per exchange; N-to-1 burst compression; G2 containment at baseline"
)

#: Baseline window: calendar month August 2026, happyranch org (seq 85-87).
RATIFIED_BASELINE_START = "2026-08-01T00:00:00Z"
RATIFIED_BASELINE_END = "2026-09-01T00:00:00Z"

#: Required criteria (quoted, founder-approved inputs — never re-derived).
REQUIRED_G1 = (
    "mentioned-message decline rate moves from 293/499 (58.7%) toward "
    "~24/204 (~12%) among retained wakes"
)
REQUIRED_G2 = (
    "zero losses across the 698 baseline founder messages that received replies"
)
REQUIRED_G3 = "report only; expect ≈65% (not a gate)"

_STATUS_DECLINED = "declined"
_STATUS_CONSUMED = "consumed"
_PURPOSE_REPLY = "reply"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def parse_timestamp(value: str) -> datetime:
    """Parse a stored ISO-8601 UTC timestamp (accepts both ``Z`` and
    ``+00:00`` suffixes; stored values are
    ``datetime.now(timezone.utc).isoformat()``)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def add_calendar_months(dt: datetime, months: int) -> datetime:
    """Add whole calendar months, clamping the day to the target month end —
    the GH-688 Phase-1 checklist §1 one-month convention."""
    month_index = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Read-only connection
# ---------------------------------------------------------------------------

def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open the org SQLite database read-only via a ``mode=ro`` URI.

    The daemon may be running concurrently (WAL mode supports concurrent
    readers); a brief read is fine and matches the Phase-1 checklist §5
    convention. Never mutates.
    """
    uri = "file:" + urllib.parse.quote(str(Path(db_path).resolve())) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Population helpers
# ---------------------------------------------------------------------------

def _mentioned_agents_from_signal(signal: str | None) -> tuple[list[str] | None, bool]:
    """Parse a persisted ``mentions_json`` value.

    Returns ``(agents, malformed)``:
      * ``None`` → no signal (legacy/system rows, ``NULL``, ``''``, ``null``).
      * ``[]``  → zero valid mentions (broadcast fallback).
      * ``["alice", ...]`` → the canonical valid set (live-mode mentioned message).
    Malformed JSON or a non-list value is counted as malformed (never
    fabricated into a mention).
    """
    if signal is None:
        return None, False
    stripped = signal.strip()
    if stripped == "" or stripped == "null":
        return None, False
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(parsed, list):
        return None, True
    names = [str(item) for item in parsed if str(item).strip()]
    return names, False


def _roster_at_write(conn: sqlite3.Connection, thread_id: str, created_at: str) -> list[str]:
    """Reconstruct the thread roster at a message's write time.

    Deterministic approximation: current ``thread_participants`` rows whose
    ``added_at <= created_at``. Documented limitation: removed participants
    are deleted from the table, so this UNDER-approximates the true
    write-time roster (a mention of a later-removed participant classifies
    as invalid → broadcast fallback in replay mode). The live window uses
    the persisted write-time signal and is unaffected.
    """
    rows = conn.execute(
        "SELECT agent_name FROM thread_participants "
        "WHERE thread_id = ? AND added_at <= ? ORDER BY added_at, agent_name",
        (thread_id, created_at),
    ).fetchall()
    return [r["agent_name"] for r in rows]


def _pct(part: int, total: int) -> float | None:
    """Percentage with an honest zero-denominator result: ``None`` (not
    measurable), never a fabricated 0."""
    if total <= 0:
        return None
    return round(100.0 * part / total, 1)


def _terminal_decline_before(row: sqlite3.Row, cutoff: datetime) -> bool:
    """True when the row is a terminal decline observable by the half-open
    cutoff: ``status == 'declined'`` AND ``consumed_at`` non-null AND
    strictly earlier than ``cutoff``.

    ``consumed_at`` is the schema's single terminal-time stamp — every
    production decline path (``Database.mark_invocation_declined``, the
    per-agent bulk decline, the runner settle paths) sets
    ``status='declined'`` and stamps ``consumed_at`` in the SAME UPDATE, and
    there is no separate ``declined_at`` column (see
    ``Database.list_invocations_for_thread_grouped_by_seq``). A wake enqueued
    before the cutoff but declined at or after it stays in the wake
    denominator (its ``enqueued_at`` is inside the window) yet never
    retrospectively counts as a decline in an interim record claiming the
    earlier observation instant; a later ``as_of`` deterministically admits
    the settled outcome.
    """
    if row["status"] != _STATUS_DECLINED:
        return False
    consumed_at = row["consumed_at"]
    if not consumed_at:
        return False
    return parse_timestamp(consumed_at) < cutoff


def _seq_covered(
    ranges: list[tuple[str, str, int, int]], thread_id: str, seq: int,
) -> bool:
    """True when any consumed-REPLY range covers ``seq`` in ``thread_id``.

    Ranges are inclusive ``[from_seq, through_seq]`` — the authoritative
    claimed/settled coverage of a coalesced wake."""
    return any(
        tid == thread_id and from_seq <= seq <= through_seq
        for (tid, _agent, from_seq, through_seq) in ranges
    )


def _parse_positive_seq(value: Any) -> int | None:
    """Parse an audit payload sequence field fail-closed.

    Returns the value only when it is a positive JSON integer (``int`` but
    not ``bool``); ``None`` for a missing, non-integer (string, float),
    boolean-like, zero, or negative value. Production payloads always carry
    ``int`` seqs (the mint/claim/recovery code ``json.dumps`` the message
    seq directly), so anything else is out-of-shape data — the caller skips
    the payload entirely rather than crash or fabricate an attribution.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _valid_range(from_seq: Any, through_seq: Any) -> tuple[int, int] | None:
    """Validate an inclusive ``[from_seq, through_seq]`` range fail-closed.

    Returns ``(from_seq, through_seq)`` only when both are positive
    non-boolean ints AND ``from_seq <= through_seq`` (an internally valid
    inclusive range — every production path writes
    ``from_seq = acknowledged + 1 <= through_seq = required``). ``None`` for
    a missing, non-integer, boolean-like, float, string, non-positive, or
    inverted range — the caller skips the payload (never fabricates
    attribution/coverage).
    """
    lo = _parse_positive_seq(from_seq)
    hi = _parse_positive_seq(through_seq)
    if lo is None or hi is None:
        return None
    if lo > hi:
        return None
    return lo, hi


def _decode_audit_payload(raw: Any) -> dict[str, Any] | None:
    """Decode an ``audit_log.payload`` value to the object its consumer reads.

    This is the SINGLE shared decoder for every audit_log payload consumer in
    this module (created / recovered / claimed maps). It returns the parsed
    payload only when it is a JSON **object** (``dict``); it safely skips a
    ``NULL``/empty payload, undecodable JSON, JSON ``null``, strings, numbers,
    booleans, and lists — ``None`` in every case. Consumers therefore never
    call ``.get`` on anything but a real object, so one malformed audit row
    can never abort a measurement run and never fabricates or reassigns an
    attribution (the row simply contributes no evidence; the caller keeps its
    documented fallback/under-approximation).
    """
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _creating_arrival_seqs(
    conn: sqlite3.Connection,
    invocations: list[sqlite3.Row],
) -> dict[str, int]:
    """Map each REPLY invocation token to the seq of the conversational
    arrival that MINTED it, from the authoritative
    ``thread_reply_wake_created`` audit trail (GH-688 Phase 1 Slice C emits
    exactly one immutable created audit per mint).

    Production semantics (``Database._apply_arrival_uncommitted``): a wake is
    minted only when a message arrival finds no queued/running ownership for
    the pair; BOTH mint paths record ``through_seq = the message seq being
    processed`` — the creating arrival. ``triggering_seq`` is the minted
    range's FLOOR (``acknowledged + 1``), which can be an earlier message
    that NEVER woke this agent (a gap in the pair's coverage): the live
    mentioned-wake attribution must use the creating arrival, never the
    floor. A ``thread_reply_wake_coalesced`` audit carries NO token and mints
    NOTHING — a coalescing arrival never creates or re-attributes a wake.

    Invocations with no created audit are absent from this map. Callers
    consult ``_recovered_replacement_seqs`` next — a restart-minted
    replacement carries no created audit, but its
    ``thread_reply_wake_recovered`` (``replacement_queued``) audit identifies
    the wake-causing arrival (the recovered ``through_seq``). Only then do
    callers fall back to ``triggering_seq`` for genuinely unattributable rows
    (a follow-on minted at settlement — whose floor IS a genuine wake-causing
    arrival, since the retained range contains only arrivals that raised the
    pair's required watermark — or a legacy pre-audit row).

    Evidence is keyed by the production ownership tuple
    ``(thread_id, agent_name, token_prefix)`` — the created audit's
    ``audit_log.agent`` column IS the wake owner — never a weaker key: an
    unrelated same-thread audit with the same 8-char prefix but a different
    owner cannot reattribute an invocation. Payloads are decoded through the
    shared ``_decode_audit_payload`` (objects only — ``NULL``/empty,
    undecodable, JSON ``null``, scalar, and list payloads are skipped before
    any field access), then parsed fail-closed
    (missing/non-8-char ``token_prefix``, or a missing/non-integer/
    boolean-like/non-positive/inverted ``from_seq``/``through_seq``) and
    skipped without exception — never a crash, never a fabricated
    attribution.
    """
    created_by_owner: dict[tuple[str, str, str], int] = {}
    for row in conn.execute(
        "SELECT task_id, agent, payload FROM audit_log "
        "WHERE action = 'thread_reply_wake_created'",
    ).fetchall():
        payload = _decode_audit_payload(row["payload"])
        if payload is None:
            continue
        prefix = payload.get("token_prefix")
        if not isinstance(prefix, str) or len(prefix) != 8:
            continue
        rng = _valid_range(payload.get("from_seq"), payload.get("through_seq"))
        if rng is None:
            continue
        created_by_owner[(row["task_id"], row["agent"], prefix)] = rng[1]
    return {
        row["invocation_token"]: created_by_owner[
            (row["thread_id"], row["agent_name"],
             (row["invocation_token"] or "")[:8])
        ]
        for row in invocations
        if (row["thread_id"], row["agent_name"],
            (row["invocation_token"] or "")[:8])
        in created_by_owner
    }


def _recovered_replacement_seqs(
    conn: sqlite3.Connection,
    invocations: list[sqlite3.Row],
) -> dict[str, int]:
    """Map each REPLY invocation token that restart recovery MINTED to the
    authoritative recovered ``through_seq`` — the pair's required watermark
    at recovery, i.e. the LAST conversational arrival that woke the agent
    before the daemon restart.

    Production semantics (``Database.recover_thread_reply_delivery_state``):
    when an interrupted in-flight attempt is recoverable, the pending receipt
    is terminalized as ``failed/daemon_restart`` and EXACTLY ONE replacement
    REPLY is minted with ``triggering_seq = acknowledged + 1`` (the retained
    range floor), while the ``thread_reply_wake_recovered`` audit records
    ``kind='replacement_queued'``, ``from_seq = acknowledged + 1``,
    ``through_seq = required``, and the replacement's 8-char
    ``token_prefix``. The replacement has NO ``thread_reply_wake_created``
    audit (recovery mints outside ``_apply_arrival_uncommitted``), so without
    this map its only attribution evidence would be the floor — which can be
    an earlier MENTIONED message that never woke the agent while the actual
    wake-causing arrival (the recovered ``through_seq``) was an unmentioned
    broadcast. The live mentioned-wake attribution must therefore use the
    recovered ``through_seq`` for restart-minted replacements.

    ``retained_queued`` recoveries are deliberately EXCLUDED: the retained
    receipt was minted by a real arrival (it carries its own created audit,
    which callers consult with higher precedence), and re-attributing it to
    the recovery-time watermark would point at a coalesced arrival that
    "mints nothing".

    Evidence is keyed by the production ownership tuple
    ``(thread_id, agent_name, token_prefix)`` — the recovery audit's
    ``audit_log.agent`` column IS the wake owner — never a weaker key: an
    unrelated same-thread audit with the same 8-char prefix owned by a
    DIFFERENT agent can never reattribute the target agent's invocation (the
    invocation lookup uses the identical tuple from its ``agent_name``).
    Payloads are decoded through the shared ``_decode_audit_payload``
    (objects only — ``NULL``/empty, undecodable, JSON ``null``, scalar, and
    list payloads are skipped before any field access), then parsed
    fail-closed: a missing kind, any non-
    ``replacement_queued`` kind, a missing/non-8-char ``token_prefix``,
    or a missing/non-integer/boolean-like/non-positive/inverted
    ``from_seq``/``through_seq`` is skipped without exception — never a
    crash, never a fabricated attribution. Invocations absent from the map
    keep their caller fallback (``triggering_seq``) for genuinely
    unattributable rows (follow-on, retained/legacy).
    """
    recovered_by_owner: dict[tuple[str, str, str], int] = {}
    for row in conn.execute(
        "SELECT task_id, agent, payload FROM audit_log "
        "WHERE action = 'thread_reply_wake_recovered'",
    ).fetchall():
        payload = _decode_audit_payload(row["payload"])
        if payload is None:
            continue
        if payload.get("kind") != "replacement_queued":
            continue
        prefix = payload.get("token_prefix")
        if not isinstance(prefix, str) or len(prefix) != 8:
            continue
        rng = _valid_range(payload.get("from_seq"), payload.get("through_seq"))
        if rng is None:
            continue
        recovered_by_owner[(row["task_id"], row["agent"], prefix)] = rng[1]
    return {
        row["invocation_token"]: recovered_by_owner[
            (row["thread_id"], row["agent_name"],
             (row["invocation_token"] or "")[:8])
        ]
        for row in invocations
        if (row["thread_id"], row["agent_name"],
            (row["invocation_token"] or "")[:8])
        in recovered_by_owner
    }


def _consumed_reply_ranges(
    conn: sqlite3.Connection,
    invocations: list[sqlite3.Row],
    *,
    enqueued_start: datetime,
    enqueued_end: datetime,
    consumed_before: datetime | None,
) -> list[tuple[str, str, int, int]]:
    """Inclusive ``(thread_id, agent_name, from_seq, through_seq)`` coverage
    ranges of every ``purpose='reply'`` invocation that reached
    ``status='consumed'`` with ``enqueued_at`` in the half-open enqueued
    window, observable by ``consumed_before`` (``None`` = no bound).

    GH-688 Phase-1 coalescing means one consumed REPLY presents/handles an
    inclusive sequence range. The authoritative immutable claimed range is
    captured at claim time: ``thread_reply_wake_claimed`` audit
    ``from_seq``/``through_seq``, matched to the invocation by its 8-char
    ``token_prefix`` within the same ``(thread_id, agent_name)`` pair
    (the audit deliberately stores only the prefix). The range floor equals
    the invocation's ``triggering_seq`` (every mint path seeds it as
    ``acknowledged + 1`` and the watermark cannot move between mint and
    claim). Claimed payloads are decoded through the shared
    ``_decode_audit_payload`` (objects only — ``NULL``/empty, undecodable,
    JSON ``null``, scalar, and list payloads are skipped before any field
    access), then parsed fail-closed (missing/non-8-char
    ``token_prefix``, or missing/non-integer/boolean-like/non-positive/
    inverted range fields are skipped without exception — never a crash, and
    an out-of-shape row simply contributes no coverage). A consumed
    invocation with no matching claim audit — a queued-settled wake that was
    never claimed, a legacy pre-coalescing row, or a skipped malformed claim
    — covers only its own ``triggering_seq``: an honest
    under-approximation that never fabricates coverage.
    """
    claimed_by_pair_prefix: dict[tuple[str, str, str], tuple[int, int]] = {}
    for row in conn.execute(
        "SELECT task_id, agent, payload FROM audit_log "
        "WHERE action = 'thread_reply_wake_claimed'",
    ).fetchall():
        payload = _decode_audit_payload(row["payload"])
        if payload is None:
            continue
        prefix = payload.get("token_prefix")
        if not isinstance(prefix, str) or len(prefix) != 8:
            continue
        rng = _valid_range(payload.get("from_seq"), payload.get("through_seq"))
        if rng is None:
            continue
        claimed_by_pair_prefix[(row["task_id"], row["agent"], prefix)] = rng

    ranges: list[tuple[str, str, int, int]] = []
    for row in invocations:
        if row["purpose"] != _PURPOSE_REPLY:
            continue
        if row["status"] != _STATUS_CONSUMED:
            continue
        enqueued = parse_timestamp(row["enqueued_at"])
        if not (enqueued_start <= enqueued < enqueued_end):
            continue
        if consumed_before is not None:
            consumed_at = row["consumed_at"]
            if not consumed_at:
                continue
            if not (parse_timestamp(consumed_at) < consumed_before):
                continue
        token = row["invocation_token"] or ""
        claimed = claimed_by_pair_prefix.get(
            (row["thread_id"], row["agent_name"], token[:8]),
        )
        trig = int(row["triggering_seq"])
        if claimed is None:
            ranges.append((row["thread_id"], row["agent_name"], trig, trig))
        else:
            ranges.append(
                (row["thread_id"], row["agent_name"], claimed[0], claimed[1]),
            )
    return ranges


# ---------------------------------------------------------------------------
# Live (post-change) measurement over [epoch, window_end)
# ---------------------------------------------------------------------------

@dataclass
class LiveMeasurement:
    """Observed values over the half-open measurement window. ``interim`` is
    True while ``as_of < window_end`` — such values are mechanism evidence
    only, never a release result (seq 129)."""

    epoch: str
    window_start: str
    window_end: str
    as_of: str
    interim: bool
    empty_window: bool
    malformed_mentions_json: int
    # G1 — mentioned-message saving
    mentioned_messages: int
    mentioned_wakes: int
    mentioned_declines: int
    mentioned_decline_rate_pct: float | None
    # G2 — founder-message coverage
    founder_messages: int
    founder_covered: int
    founder_uncovered: int
    # G3 — org-wide decline rate (report only)
    org_wakes: int
    org_declines: int
    org_decline_rate_pct: float | None
    notes: list[str] = field(default_factory=list)


def measure_live_window(
    conn: sqlite3.Connection,
    *,
    epoch: str,
    window_months: int = 1,
    as_of: str | None = None,
) -> LiveMeasurement:
    """Compute the three ratified metrics over ``[epoch, epoch + window_months
    calendar months)`` using the persisted write-time mention signal. Read-only
    and deterministic given the connection contents."""
    window_start = parse_timestamp(epoch)
    window_end = add_calendar_months(window_start, window_months)
    as_of_dt = parse_timestamp(as_of) if as_of else utc_now()
    # The observation cutoff: every population/diagnostic that claims the
    # observation instant is bounded by min(as_of, window_end) (half-open).
    # An interim run never includes rows after its stated as_of; a completed
    # window (as_of >= window_end) still caps at window_end.
    observation_end = min(as_of_dt, window_end)

    # Mentioned messages (live): kind='message' with a non-empty mentions_json.
    mentioned: dict[tuple[str, int], bool] = {}  # (thread_id, seq) -> mentioned
    founder: list[tuple[str, int]] = []
    malformed = 0
    for row in conn.execute(
        "SELECT thread_id, seq, speaker, kind, mentions_json, created_at "
        "FROM thread_messages",
    ).fetchall():
        if row["kind"] != "message":
            continue
        created = parse_timestamp(row["created_at"])
        if not (window_start <= created < observation_end):
            continue
        key = (row["thread_id"], row["seq"])
        if row["speaker"] == "founder":
            founder.append(key)
        agents, is_malformed = _mentioned_agents_from_signal(row["mentions_json"])
        if is_malformed:
            malformed += 1
        mentioned[key] = bool(agents)

    invocations = [
        row for row in conn.execute(
            "SELECT thread_id, agent_name, invocation_token, triggering_seq, "
            "purpose, status, enqueued_at, consumed_at FROM thread_invocations",
        ).fetchall()
    ]
    reply_in_window = [
        row for row in invocations
        if row["purpose"] == _PURPOSE_REPLY
        and window_start <= parse_timestamp(row["enqueued_at"]) < observation_end
    ]
    # G1 mentioned-wake attribution: each wake is attributed to the message
    # arrival that MINTED it (the authoritative created-audit through_seq), NOT
    # its triggering_seq — GH-688 Phase-1 coalescing makes triggering_seq the
    # retained range floor, which can be an earlier never-waking message.
    creating = _creating_arrival_seqs(conn, reply_in_window)
    recovered = _recovered_replacement_seqs(conn, reply_in_window)
    org_wakes = len(reply_in_window)
    # Terminal declines are observable at the observation instant only when
    # consumed_at is non-null and strictly earlier than the half-open cutoff
    # (see _terminal_decline_before) — a wake enqueued before the cutoff but
    # declined at/after it stays in the denominator, never the numerator.
    org_declines = sum(
        1 for row in reply_in_window
        if _terminal_decline_before(row, observation_end)
    )

    mentioned_wakes = [
        row for row in reply_in_window
        if mentioned.get(
            (
                row["thread_id"],
                # GH-688 Phase-1 coalescing makes triggering_seq the retained
                # range floor (acknowledged+1), which can be an earlier
                # message that NEVER woke this agent — a later broadcast wake
                # would be falsely attributed to an earlier mention. Attribute
                # each wake to the arrival that MINTED it, with explicit
                # precedence: (1) the created audit's through_seq (the
                # authoritative minting arrival); (2) a recovery replacement's
                # recovered through_seq (the wake-causing arrival for a
                # restart-minted replacement, which has no created audit);
                # (3) triggering_seq — genuinely unattributable rows
                # (follow-on minted at settlement, retained/legacy pre-audit).
                creating.get(
                    row["invocation_token"],
                    recovered.get(
                        row["invocation_token"], row["triggering_seq"],
                    ),
                ),
            ),
            False,
        )
    ]
    mentioned_declines = sum(
        1 for row in mentioned_wakes
        if _terminal_decline_before(row, observation_end)
    )

    # G2 coverage: founder message covered iff a consumed REPLY's authoritative
    # claimed/settled inclusive delivery range contains its sequence (the
    # "698 founder messages with ≥1 consumed REPLY" definition, TASK-5647 §8,
    # read range-aware: Phase-1 coalescing lets ONE consumed REPLY cover an
    # inclusive sequence range — see _consumed_reply_ranges). Only REPLYs
    # observable by the observation instant count.
    consumed_ranges = _consumed_reply_ranges(
        conn,
        invocations,
        enqueued_start=window_start,
        enqueued_end=observation_end,
        consumed_before=observation_end,
    )
    founder_covered = sum(
        1 for (tid, seq) in founder if _seq_covered(consumed_ranges, tid, seq)
    )
    founder_uncovered = len(founder) - founder_covered

    notes: list[str] = []
    if len(mentioned_wakes) == 0:
        notes.append(
            "G1 denominator is zero (no mentioned-message REPLY wakes in "
            "window) — not measurable, not a failure."
        )
    if founder:
        if founder_uncovered:
            notes.append(
                f"G2: {founder_uncovered} in-window founder message(s) have no "
                "consumed REPLY covering them yet — interim only while the "
                "window is open."
            )
    else:
        notes.append("G2 denominator is zero (no founder messages in window).")
    if org_wakes == 0:
        notes.append("G3 denominator is zero (no REPLY wakes in window).")
    if malformed:
        notes.append(
            f"{malformed} in-window message(s) carried a malformed mentions_json; "
            "treated as not-mentioned (never fabricated)."
        )
    interim = as_of_dt < window_end
    if interim:
        notes.append(
            "Window incomplete (as_of < window_end): every observed value is "
            "interim — mechanism evidence only, not a release result (THR-198 "
            "seq 129)."
        )

    return LiveMeasurement(
        epoch=epoch,
        window_start=_iso_z(window_start),
        window_end=_iso_z(window_end),
        as_of=_iso_z(as_of_dt),
        interim=interim,
        empty_window=(org_wakes == 0 and not founder),
        malformed_mentions_json=malformed,
        mentioned_messages=sum(1 for v in mentioned.values() if v),
        mentioned_wakes=len(mentioned_wakes),
        mentioned_declines=mentioned_declines,
        mentioned_decline_rate_pct=_pct(mentioned_declines, len(mentioned_wakes)),
        founder_messages=len(founder),
        founder_covered=founder_covered,
        founder_uncovered=founder_uncovered,
        org_wakes=org_wakes,
        org_declines=org_declines,
        org_decline_rate_pct=_pct(org_declines, org_wakes),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Replay (pre-change baseline + Phase-2 projection + zero-loss) — TASK-5647 §8
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    """Baseline reproduction + Phase-2 projection over a pre-change window.

    ``projected_decline_rate_pct`` is the deterministic expectation for the
    G1 gate over that window (agent declined ⇔ it declined its baseline wake
    for the same message); ``genuine_violations = zero_loss_violations -
    zero_loss_artifact_candidates`` must be 0.
    """

    window_start: str
    window_end: str
    # baseline reproduction
    mentioned_messages: int
    baseline_mentioned_wakes: int
    baseline_mentioned_declines: int
    baseline_mentioned_decline_rate_pct: float | None
    founder_replied_messages: int
    org_wakes: int
    org_declines: int
    org_decline_rate_pct: float | None
    # Phase-2 projection
    retained_wakes: int
    projected_declines: int
    projected_decline_rate_pct: float | None
    zero_loss_violations: int
    #: Violations where EVERY baseline replier is absent from the reconstructed
    #: write-time roster — a roster-under-approximation artifact (removed /
    #: re-added participants), not a Phase-2 routing failure. Honest
    #: separation: ``genuine_violations = zero_loss_violations -
    #: zero_loss_artifact_candidates``.
    zero_loss_artifact_candidates: int
    limitations: list[str] = field(default_factory=list)


def replay_baseline(
    conn: sqlite3.Connection,
    *,
    window_start: str,
    window_end: str,
) -> ReplayResult:
    """Reproduce the founder's baseline measurement over a pre-change window
    and project the Phase-2 outcome (TASK-5647 §8 method, THR-198 seq 87).

    Roster at write time is reconstructed from current participants with
    ``added_at <= created_at`` (documented under-approximation). The Phase-2
    wake set uses ``resolve_wake_set`` under UNCONDITIONAL mention routing
    (TASK-6027 founder ruling — the persisted ``threads.mention_routing_enabled``
    column is inert legacy and is not consulted). Read-only and deterministic.
    """
    start = parse_timestamp(window_start)
    end = parse_timestamp(window_end)

    messages = [
        row for row in conn.execute(
            "SELECT thread_id, seq, speaker, kind, body_markdown, created_at "
            "FROM thread_messages",
        ).fetchall()
    ]
    in_window = [
        row for row in messages
        if row["kind"] == "message"
        and start <= parse_timestamp(row["created_at"]) < end
    ]

    invocations = [
        row for row in conn.execute(
            "SELECT thread_id, agent_name, invocation_token, triggering_seq, "
            "purpose, status, enqueued_at, consumed_at FROM thread_invocations",
        ).fetchall()
    ]
    reply_in_window = [
        row for row in invocations
        if row["purpose"] == _PURPOSE_REPLY
        and start <= parse_timestamp(row["enqueued_at"]) < end
    ]
    # In-window outcome map: the founder's replay scored "every August reply
    # invocation that reached a terminal outcome" — windowed, like every
    # population here.
    outcome = {
        (row["thread_id"], row["triggering_seq"], row["agent_name"]): row["status"]
        for row in reply_in_window
    }

    from runtime.daemon.thread_mentions import (  # pure, local
        parse_mentions, resolve_wake_set, valid_mentions,
    )

    mentioned: set[tuple[str, int]] = set()
    wake_sets: dict[tuple[str, int], list[str]] = {}
    for row in in_window:
        roster = _roster_at_write(conn, row["thread_id"], row["created_at"])
        parsed = parse_mentions(row["body_markdown"])
        valid = valid_mentions(parsed, roster, row["speaker"])
        if valid:
            mentioned.add((row["thread_id"], row["seq"]))
        wake_sets[(row["thread_id"], row["seq"])] = resolve_wake_set(
            parsed, roster, row["speaker"],
        )

    mentioned_wakes = [
        row for row in reply_in_window
        if (row["thread_id"], row["triggering_seq"]) in mentioned
    ]
    mentioned_declines = sum(
        1 for row in mentioned_wakes if row["status"] == _STATUS_DECLINED
    )
    org_declines = sum(1 for row in reply_in_window if row["status"] == _STATUS_DECLINED)

    # Phase-2 projection: retained wakes = valid-mention wakes; a retained
    # wake is predicted to decline iff that agent's baseline wake for the
    # same message declined (deterministic mapping, TASK-5647 §8).
    retained = 0
    projected_declines = 0
    for row in in_window:
        key = (row["thread_id"], row["seq"])
        if key not in mentioned:
            continue
        for agent in wake_sets[key]:
            retained += 1
            if outcome.get((row["thread_id"], row["seq"], agent)) == _STATUS_DECLINED:
                projected_declines += 1

    # Zero-loss (G2): every founder message with ≥1 baseline consumed REPLY
    # must keep ≥1 replier under the Phase-2 wake set. A violation whose
    # baseline repliers are ALL absent from the reconstructed write-time
    # roster is a roster-under-approximation artifact (removed/re-added
    # participants), not a routing failure — counted separately so the record
    # never presents reconstruction noise as a genuine silence risk.
    # Baseline replying set is derived from the authoritative claimed ranges
    # of the window's consumed REPLYs (range-aware: a coalesced founder
    # message is covered by the wake that claimed it — the 698-style
    # population, TASK-5647 §8), never from the Phase-2 wake set.
    consumed_ranges = _consumed_reply_ranges(
        conn,
        invocations,
        enqueued_start=start,
        enqueued_end=end,
        consumed_before=None,
    )
    violations = 0
    artifact_candidates = 0
    founder_replied = 0
    for row in in_window:
        if row["speaker"] != "founder":
            continue
        key = (row["thread_id"], row["seq"])
        baseline_repliers = {
            agent
            for (tid, agent, from_seq, through_seq) in consumed_ranges
            if tid == key[0] and from_seq <= key[1] <= through_seq
        }
        if not baseline_repliers:
            continue  # not part of the 698-style population
        founder_replied += 1
        if set(wake_sets[key]) & baseline_repliers:
            continue
        violations += 1
        roster = _roster_at_write(conn, row["thread_id"], row["created_at"])
        if not (set(roster) & baseline_repliers):
            artifact_candidates += 1

    return ReplayResult(
        window_start=_iso_z(start),
        window_end=_iso_z(end),
        mentioned_messages=len(mentioned),
        baseline_mentioned_wakes=len(mentioned_wakes),
        baseline_mentioned_declines=mentioned_declines,
        baseline_mentioned_decline_rate_pct=_pct(mentioned_declines, len(mentioned_wakes)),
        founder_replied_messages=founder_replied,
        org_wakes=len(reply_in_window),
        org_declines=org_declines,
        org_decline_rate_pct=_pct(org_declines, len(reply_in_window)),
        retained_wakes=retained,
        projected_declines=projected_declines,
        projected_decline_rate_pct=_pct(projected_declines, retained),
        zero_loss_violations=violations,
        zero_loss_artifact_candidates=artifact_candidates,
        limitations=[
            "write-time roster is an under-approximation (removed participants "
            "are deleted from thread_participants); mentions of later-removed "
            "participants classify as invalid → broadcast in the replay",
            "zero-loss violations whose baseline repliers are ALL absent from "
            "the reconstructed write-time roster are roster artifacts, not "
            "routing failures (genuine = violations − artifact_candidates)",
        ],
    )


# ---------------------------------------------------------------------------
# Release record (observed vs required; interim honesty)
# ---------------------------------------------------------------------------

def build_release_record(
    *,
    live: LiveMeasurement | None = None,
    replay: ReplayResult | None = None,
    required_g1: str = REQUIRED_G1,
    required_g2: str = REQUIRED_G2,
    required_g3: str = REQUIRED_G3,
) -> dict[str, Any]:
    """Assemble the auditable release record. Observed values and required
    criteria are ALWAYS separate fields; the record never claims a release
    result while the window is incomplete."""
    if live is None and replay is None:
        raise ValueError("at least one of live/replay is required")
    record: dict[str, Any] = {
        "schema": "thr198-phase2-release-measurement-record/v1",
        "source": "THR-198 Phase-2 Slice D (seq 108-110; epoch seq 128/129)",
        "required": {
            "g1_mentioned_message_saving": required_g1,
            "g2_founder_coverage": required_g2,
            "g3_org_wide_decline": required_g3,
        },
        "observation": "pending",
    }
    if live is not None:
        record["observation"] = "interim" if live.interim else "complete"
        record["live"] = asdict(live)
        if live.interim:
            record["notes"] = live.notes
    if replay is not None:
        record["replay"] = asdict(replay)
    return record


def format_markdown(record: dict[str, Any]) -> str:
    """Render the record as a fillable/auditable markdown release record."""
    out: list[str] = [
        "# THR-198 Phase-2 mention routing — release record",
        "",
        f"- observation: **{record['observation']}**",
        f"- schema: `{record['schema']}`",
        f"- source: {record['source']}",
        "",
        "## Required criteria (ratified, quoted)",
        "",
    ]
    for key, value in record["required"].items():
        out.append(f"- **{key}**: {value}")
    live = record.get("live")
    if live is not None:
        out += [
            "",
            "## Observed (live window)",
            "",
            f"- window: `[{live['window_start']} .. {live['window_end']})`",
            f"- as_of: {live['as_of']}",
            f"- interim: {live['interim']}",
            f"- empty_window: {live['empty_window']}",
            f"- malformed_mentions_json: {live['malformed_mentions_json']}",
            "",
            "### G1 — mentioned-message saving",
            f"- mentioned_messages: {live['mentioned_messages']}",
            f"- wakes: {live['mentioned_wakes']}",
            f"- declines: {live['mentioned_declines']}",
            f"- decline_rate_pct: {live['mentioned_decline_rate_pct']}",
            "",
            "### G2 — founder-message coverage",
            f"- founder_messages: {live['founder_messages']}",
            f"- covered (≥1 consumed REPLY covering it): {live['founder_covered']}",
            f"- uncovered (interim): {live['founder_uncovered']}",
            "",
            "### G3 — org-wide decline (report only)",
            f"- wakes: {live['org_wakes']}",
            f"- declines: {live['org_declines']}",
            f"- decline_rate_pct: {live['org_decline_rate_pct']}",
            "",
        ]
        if live.get("notes"):
            out += ["### Notes", ""]
            out += [f"- {note}" for note in live["notes"]]
            out.append("")
    replay = record.get("replay")
    if replay is not None:
        out += [
            "## Baseline reproduction + Phase-2 projection (replay)",
            "",
            f"- window: `[{replay['window_start']} .. {replay['window_end']})`",
            "",
            "### Baseline (pre-change)",
            f"- mentioned_messages: {replay['mentioned_messages']}",
            f"- mentioned-message wakes: {replay['baseline_mentioned_wakes']}",
            f"- mentioned-message declines: {replay['baseline_mentioned_declines']}",
            f"- mentioned-message decline_rate_pct: {replay['baseline_mentioned_decline_rate_pct']}",
            f"- founder messages with ≥1 consumed REPLY: {replay['founder_replied_messages']}",
            f"- org-wide wakes / declines / rate: {replay['org_wakes']} / "
            f"{replay['org_declines']} / {replay['org_decline_rate_pct']}",
            "",
            "### Phase-2 projection (deterministic replay)",
            f"- retained wakes: {replay['retained_wakes']}",
            f"- projected declines: {replay['projected_declines']}",
            f"- projected decline_rate_pct: {replay['projected_decline_rate_pct']}",
            f"- zero-loss violations: {replay['zero_loss_violations']}",
            f"- zero-loss artifact candidates (roster under-approximation): "
            f"{replay['zero_loss_artifact_candidates']}",
            "",
        ]
        if replay.get("limitations"):
            out += ["### Limitations", ""]
            out += [f"- {limitation}" for limitation in replay["limitations"]]
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m runtime.infrastructure.thread_release_measurement",
        description=(
            "THR-198 Phase-2 Slice D read-only release measurement. Opens the "
            "org SQLite database read-only (mode=ro) and emits the ratified "
            "measurement record; writes nothing to the database."
        ),
    )
    parser.add_argument("--db", required=True, help="path to <org>/happyranch.db")
    parser.add_argument(
        "--epoch", default=RATIFIED_EPOCH,
        help=f"Phase-2 deployment epoch (default {RATIFIED_EPOCH})",
    )
    parser.add_argument("--window-months", type=int, default=1)
    parser.add_argument(
        "--as-of",
        default=None,
        help="observation instant (UTC ISO-8601); default now",
    )
    parser.add_argument("--mode", choices=["live", "replay", "all"], default="all")
    parser.add_argument("--baseline-start", default=RATIFIED_BASELINE_START)
    parser.add_argument("--baseline-end", default=RATIFIED_BASELINE_END)
    parser.add_argument("--out-json", help="write the JSON record to this path")
    parser.add_argument("--out-md", help="write the markdown record to this path")
    args = parser.parse_args(argv)

    conn = open_readonly(args.db)
    try:
        live = None
        replay = None
        if args.mode in ("live", "all"):
            live = measure_live_window(
                conn,
                epoch=args.epoch,
                window_months=args.window_months,
                as_of=args.as_of,
            )
        if args.mode in ("replay", "all"):
            replay = replay_baseline(
                conn,
                window_start=args.baseline_start,
                window_end=args.baseline_end,
            )
    finally:
        conn.close()

    record = build_release_record(live=live, replay=replay)
    text = json.dumps(record, indent=2, ensure_ascii=False)
    if args.out_json:
        Path(args.out_json).write_text(text + "\n", encoding="utf-8")
    if args.out_md:
        Path(args.out_md).write_text(format_markdown(record), encoding="utf-8")
    if not args.out_json and not args.out_md:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
