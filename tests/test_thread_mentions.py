"""Phase-2 mention routing (THR-198) Slice A — pure parser/resolver matrix.

Covers the ratified routing contract (THR-198 seq 108-110) for the pure
module only:

    resolve_wake_set(mentioned, participants, speaker, enabled):
      * disabled                        -> participants - speaker (broadcast)
      * enabled + valid mentions        -> exactly that valid set
      * enabled + zero valid mentions   -> participants - speaker (fallback)
        (including invalid/nonparticipant-only and self-only bodies)

Slice A does NOT wire this into production wake routing — the resolver is
shipped, tested, and left unconnected until Slice B. The store persists the
valid participant subset (``valid_mentions``) as ``mentions_json``.
"""
from __future__ import annotations

from runtime.daemon.thread_mentions import (
    parse_mentions,
    resolve_wake_set,
    valid_mentions,
)


# ---------------------------------------------------------------------------
# parse_mentions — @token extraction
# ---------------------------------------------------------------------------


def test_parse_none_and_empty_body():
    assert parse_mentions(None) == []
    assert parse_mentions("") == []


def test_parse_no_mentions():
    assert parse_mentions("plain text, no at-signs") == []
    assert parse_mentions("@") == []


def test_parse_single_mention():
    assert parse_mentions("hello @dev_agent please look") == ["dev_agent"]


def test_parse_multiple_mentions_preserves_order():
    assert parse_mentions("@alpha and @bravo") == ["alpha", "bravo"]


def test_parse_dedupes_stable():
    assert parse_mentions("@alpha @bravo @alpha") == ["alpha", "bravo"]
    assert parse_mentions("@bravo @alpha @bravo @alpha") == ["bravo", "alpha"]


def test_parse_trailing_punctuation_stripped():
    assert parse_mentions("thanks @dev_agent.") == ["dev_agent"]
    assert parse_mentions("thanks @dev_agent,") == ["dev_agent"]
    assert parse_mentions("thanks @dev_agent:") == ["dev_agent"]
    assert parse_mentions("thanks @dev_agent!") == ["dev_agent"]


def test_parse_agent_name_charset():
    assert parse_mentions("@code_reviewer @qa_engineer") == [
        "code_reviewer", "qa_engineer",
    ]
    assert parse_mentions("@hyphen-agent @dot.agent") == ["hyphen-agent", "dot.agent"]


def test_parse_founder_literal_is_a_token():
    # @founder parses as a mention token; the resolver treats it as invalid
    # because the founder is never a participant row.
    assert parse_mentions("@founder please decide") == ["founder"]


def test_parse_mentions_inside_word_boundary():
    # "abc@def" is not a mention (no word boundary before @).
    assert parse_mentions("abc@def") == ["def"]


# ---------------------------------------------------------------------------
# valid_mentions — canonical participant matching + speaker exclusion
# ---------------------------------------------------------------------------


def test_valid_filters_to_participants_only():
    mentioned = ["alpha", "bogus", "charlie"]
    assert valid_mentions(mentioned, ["alpha", "charlie"], "founder") == [
        "alpha", "charlie",
    ]


def test_valid_excludes_speaker():
    assert valid_mentions(["alpha"], ["alpha", "bravo"], "alpha") == []
    assert valid_mentions(
        ["alpha", "bravo"], ["alpha", "bravo"], "alpha",
    ) == ["bravo"]


def test_valid_dedupes_stable():
    assert valid_mentions(
        ["bravo", "alpha", "bravo"], ["alpha", "bravo"], "founder",
    ) == ["bravo", "alpha"]


def test_valid_empty_inputs():
    assert valid_mentions([], ["alpha"], "founder") == []
    assert valid_mentions(["alpha"], [], "founder") == []


# ---------------------------------------------------------------------------
# resolve_wake_set — the ratified matrix
# ---------------------------------------------------------------------------

ROSTER = ["alpha", "bravo", "charlie"]


def test_disabled_broadcasts_regardless_of_mentions():
    for mentioned in ([], ["alpha"], ["alpha", "bravo"], ["nobody"], ["alpha", "nobody"]):
        assert resolve_wake_set(
            mentioned, ROSTER, "founder", mention_routing_enabled=False,
        ) == ["alpha", "bravo", "charlie"]


def test_enabled_zero_mentions_broadcasts():
    assert resolve_wake_set(
        [], ROSTER, "founder", mention_routing_enabled=True,
    ) == ["alpha", "bravo", "charlie"]


def test_enabled_single_valid_mention_routes_to_that_agent():
    assert resolve_wake_set(
        ["bravo"], ROSTER, "founder", mention_routing_enabled=True,
    ) == ["bravo"]


def test_enabled_multiple_valid_mentions_routes_to_exactly_that_set():
    assert resolve_wake_set(
        ["charlie", "alpha"], ROSTER, "founder", mention_routing_enabled=True,
    ) == ["charlie", "alpha"]


def test_enabled_valid_plus_invalid_routes_to_valid_only():
    assert resolve_wake_set(
        ["bravo", "@founder", "nobody"], ROSTER, "founder",
        mention_routing_enabled=True,
    ) == ["bravo"]


def test_enabled_invalid_only_broadcasts():
    # @founder literal, typos, terminated/non-participant names.
    for mentioned in (["founder"], ["nobody"], ["typo_agent"], ["founder", "nobody"]):
        assert resolve_wake_set(
            mentioned, ROSTER, "founder", mention_routing_enabled=True,
        ) == ["alpha", "bravo", "charlie"]


def test_enabled_self_only_broadcasts():
    assert resolve_wake_set(
        ["bravo"], ROSTER, "bravo", mention_routing_enabled=True,
    ) == ["alpha", "charlie"]
    assert resolve_wake_set(
        ["bravo", "alpha"], ROSTER, "bravo", mention_routing_enabled=True,
    ) == ["alpha"]


def test_participant_removed_after_mention_falls_back_to_broadcast():
    # A mention of an agent who left the roster is invalid at resolve time.
    roster = ["alpha", "bravo"]
    assert resolve_wake_set(
        ["charlie"], roster, "founder", mention_routing_enabled=True,
    ) == ["alpha", "bravo"]


def test_participant_added_after_mention_becomes_valid():
    # A mention of a newly-added participant resolves to that set.
    roster = ["alpha", "bravo", "charlie"]
    assert resolve_wake_set(
        ["charlie"], roster, "founder", mention_routing_enabled=True,
    ) == ["charlie"]


def test_enabled_stable_dedup_in_wake_set():
    assert resolve_wake_set(
        ["bravo", "alpha", "bravo"], ROSTER, "founder", mention_routing_enabled=True,
    ) == ["bravo", "alpha"]


def test_speaker_never_in_own_wake_set():
    for enabled in (True, False):
        wake = resolve_wake_set(
            ["alpha", "bravo", "charlie"], ROSTER, "alpha",
            mention_routing_enabled=enabled,
        )
        assert "alpha" not in wake
