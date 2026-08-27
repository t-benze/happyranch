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
  its stated ``as_of``.
* RANGE-AWARE COVERAGE. GH-688 Phase-1 coalescing lets ONE consumed REPLY
  cover an inclusive sequence range; coverage uses the authoritative
  immutable claimed range (the ``thread_reply_wake_claimed`` audit's
  ``from_seq..through_seq``, floor = the invocation's ``triggering_seq``),
  never an exact-``triggering_seq`` join.

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


def _thread_mention_routing_enabled(conn: sqlite3.Connection, thread_id: str) -> bool:
    """Read the per-thread setting; a missing row/column defaults to enabled
    (the ratified default), so the replay never silently widens."""
    try:
        row = conn.execute(
            "SELECT mention_routing_enabled FROM threads WHERE id = ?",
            (thread_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return True  # legacy DB without the Slice-A column
    if row is None:
        return True
    return bool(row["mention_routing_enabled"])


def _pct(part: int, total: int) -> float | None:
    """Percentage with an honest zero-denominator result: ``None`` (not
    measurable), never a fabricated 0."""
    if total <= 0:
        return None
    return round(100.0 * part / total, 1)


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
    claim). A consumed invocation with no matching claim audit — a
    queued-settled wake that was never claimed, or a legacy pre-coalescing
    row — covers only its own ``triggering_seq``: an honest
    under-approximation that never fabricates coverage.
    """
    claimed_by_pair_prefix: dict[tuple[str, str, str], tuple[int, int]] = {}
    for row in conn.execute(
        "SELECT task_id, agent, payload FROM audit_log "
        "WHERE action = 'thread_reply_wake_claimed'",
    ).fetchall():
        if not row["payload"]:
            continue
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
        prefix = payload.get("token_prefix")
        from_seq = payload.get("from_seq")
        through_seq = payload.get("through_seq")
        if not prefix or from_seq is None or through_seq is None:
            continue
        claimed_by_pair_prefix[(row["task_id"], row["agent"], str(prefix))] = (
            int(from_seq),
            int(through_seq),
        )

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
    org_wakes = len(reply_in_window)
    org_declines = sum(1 for row in reply_in_window if row["status"] == _STATUS_DECLINED)

    mentioned_wakes = [
        row for row in reply_in_window
        if mentioned.get((row["thread_id"], row["triggering_seq"]), False)
    ]
    mentioned_declines = sum(
        1 for row in mentioned_wakes if row["status"] == _STATUS_DECLINED
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
    wake set uses ``resolve_wake_set`` with the persisted per-thread setting
    (all threads default-enabled). Read-only and deterministic.
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
            mention_routing_enabled=_thread_mention_routing_enabled(
                conn, row["thread_id"],
            ),
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
