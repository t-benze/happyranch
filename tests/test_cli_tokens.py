"""Tests for the ``happyranch tokens`` CLI subcommand.

Mirrors the argparse-only + ``cmd_*`` mock pattern used in
``tests/test_cli.py`` for ``cmd_audit`` — we exercise parser wiring
directly and stub out :class:`OpcClient` for the command-handler tests.
"""
from __future__ import annotations

import argparse
import json as _json
from unittest.mock import MagicMock, patch

import pytest

from cli.main import build_parser


def _parse(*args: str) -> argparse.Namespace:
    return build_parser().parse_args(list(args))


# ── argparse parsing ────────────────────────────────────────────


def test_tokens_subcommand_parses_defaults():
    ns = _parse("tokens", "--org", "myorg")
    assert ns.command == "tokens"
    assert ns.org == "myorg"
    assert ns.task_id is None
    assert ns.agent is None
    assert ns.since is None
    assert ns.limit is None
    assert ns.json is False
    assert ns.by_agent is False
    assert ns.by_task is False
    assert ns.by_thread is False


def test_tokens_subcommand_parses_filters():
    ns = _parse(
        "tokens",
        "--org", "myorg",
        "--task-id", "TASK-1",
        "--agent", "dev",
        "--since", "2026-05-01",
        "--scope-type", "thread",
        "--scope-id", "THR-001",
        "--thread-id", "THR-001",
        "--purpose", "reply",
        "--limit", "5",
        "--json",
    )
    assert ns.task_id == "TASK-1"
    assert ns.agent == "dev"
    assert ns.since == "2026-05-01"
    assert ns.scope_type == "thread"
    assert ns.scope_id == "THR-001"
    assert ns.thread_id == "THR-001"
    assert ns.purpose == "reply"
    assert ns.limit == 5
    assert ns.json is True


def test_tokens_subcommand_parses_by_agent():
    ns = _parse("tokens", "--org", "myorg", "--by-agent")
    assert ns.by_agent is True
    assert ns.by_task is False


def test_tokens_subcommand_parses_by_task():
    ns = _parse("tokens", "--org", "myorg", "--by-task")
    assert ns.by_task is True
    assert ns.by_agent is False


def test_tokens_subcommand_parses_by_thread():
    ns = _parse("tokens", "--org", "myorg", "--by-thread")
    assert ns.by_thread is True


def test_tokens_subcommand_rejects_multiple_rollups_together():
    with pytest.raises(SystemExit):
        _parse("tokens", "--org", "myorg", "--by-agent", "--by-task", "--by-thread")


# ── cmd_tokens behaviour ───────────────────────────────────────


def _mock_args(**overrides) -> argparse.Namespace:
    base = dict(
        org="myorg", task_id=None, agent=None, since=None, limit=None,
        scope_type=None, scope_id=None, thread_id=None,
        purpose=None, by_agent=False, by_task=False, by_thread=False,
        by_purpose=False, top=None, over_threshold=None,
        json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_tokens_calls_list_when_no_group_by(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = []
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args())
    fake.list_tokens.assert_called_once()
    fake.aggregate_tokens.assert_not_called()
    # Default limit applied (20) when --limit omitted.
    _, kwargs = fake.list_tokens.call_args
    assert kwargs["limit"] == 20
    assert kwargs["slug"] == "myorg"


def test_cmd_tokens_forwards_explicit_limit_and_filters():
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = []
    args = _mock_args(
        task_id="TASK-7",
        agent="dev",
        since="2026-05-01",
        limit=3,
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        purpose="reply",
    )
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(args)
    fake.list_tokens.assert_called_once_with(
        slug="myorg", task_id="TASK-7", agent="dev",
        since="2026-05-01", limit=3, scope_type="thread",
        scope_id="THR-001", thread_id="THR-001",
        purpose="reply",
    )


def test_cmd_tokens_calls_aggregate_when_by_agent():
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = []
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_agent=True))
    fake.aggregate_tokens.assert_called_once_with(
        slug="myorg", group_by="agent",
        task_id=None, agent=None, since=None, scope_type=None,
        scope_id=None, thread_id=None, purpose=None,
    )
    fake.list_tokens.assert_not_called()


def test_cmd_tokens_calls_aggregate_when_by_task():
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = []
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_task=True))
    fake.aggregate_tokens.assert_called_once_with(
        slug="myorg", group_by="task",
        task_id=None, agent=None, since=None, scope_type=None,
        scope_id=None, thread_id=None, purpose=None,
    )
    fake.list_tokens.assert_not_called()


def test_cmd_tokens_calls_aggregate_when_by_thread():
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = []
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True, thread_id="THR-001"))
    fake.aggregate_tokens.assert_called_once_with(
        slug="myorg", group_by="thread",
        task_id=None, agent=None, since=None, scope_type=None,
        scope_id=None, thread_id="THR-001", purpose=None,
    )
    fake.list_tokens.assert_not_called()


def test_cmd_tokens_default_view_renders_total_excluding_cache_reads(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = [
        {
            "id": 1,
            "task_id": "TASK-152",
            "agent": "engineering_head",
            "executor": "claude",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 9999,    # excluded from total
            "reasoning_tokens": 7,
            "created_at": "2026-05-05T14:22:11+00:00",
        },
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args())
    out = capsys.readouterr().out
    assert "TASK-152" in out
    assert "engineering_head" in out
    assert "claude" in out
    # total = 100 + 50 + 7 = 157, formatted with thousands separator
    assert "157" in out
    # cache reads shown but excluded from total (no '10,156' anywhere)
    assert "9,999" in out


def test_cmd_tokens_empty_default_view_message(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.list_tokens.return_value = []
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args())
    assert "No token usage rows" in capsys.readouterr().out


def test_cmd_tokens_json_flag_dumps_raw_rows(capsys):
    from cli.main import cmd_tokens

    rows = [{"id": 1, "task_id": "T", "agent": "a", "executor": "claude",
             "input_tokens": 5, "output_tokens": 3, "cache_read_tokens": 0,
             "reasoning_tokens": None, "created_at": "2026-05-05T00:00:00+00:00"}]
    fake = MagicMock()
    fake.list_tokens.return_value = rows
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(json=True))
    parsed = _json.loads(capsys.readouterr().out)
    assert parsed == rows


def test_cmd_tokens_json_flag_dumps_rollup(capsys):
    from cli.main import cmd_tokens

    rollup = [{"agent": "dev", "sessions": 2, "input_tokens": 10,
               "output_tokens": 5, "cache_read_tokens": 1,
               "cache_creation_tokens": 0, "reasoning_tokens": 0}]
    fake = MagicMock()
    fake.aggregate_tokens.return_value = rollup
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_agent=True, json=True))
    parsed = _json.loads(capsys.readouterr().out)
    assert parsed == rollup


# ── phase-1 leg B: --top / --over-threshold / --by-purpose / Model ──
#
# These cover the CLI surface added in TASK-270 (THR-015 Track B, spec
# §2/§3.1/§3.2/§3.3/§6). Churn invariant: every sort/threshold keys on
# total_tokens only — cache columns never participate.

from cli.commands.tasks import (  # noqa: E402
    MODEL_FIX_CUTOVER_TS,
    classify_model,
    over_threshold,
)


def _rollup_row(key_field, key, total, sessions=1, **extra):
    """A by-* rollup row with churn split into input/output so that
    total = input + output + reasoning (cache never folded in)."""
    row = {
        key_field: key,
        "sessions": sessions,
        "input_tokens": total,        # all churn lands in input for simplicity
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 9_999_999,   # large cache to prove it is NOT churn
        "cache_creation_tokens": 0,
        "total_tokens": total,
    }
    row.update(extra)
    return row


# ---- argparse parsing ----


def test_tokens_subcommand_parses_top():
    ns = _parse("tokens", "--org", "myorg", "--by-thread", "--top", "10")
    assert ns.top == 10


def test_tokens_subcommand_parses_over_threshold():
    ns = _parse("tokens", "--org", "myorg", "--by-thread", "--over-threshold", "1000000")
    assert ns.over_threshold == 1000000


def test_tokens_subcommand_parses_by_purpose():
    ns = _parse("tokens", "--org", "myorg", "--by-purpose")
    assert ns.by_purpose is True
    assert ns.by_thread is False


def test_tokens_subcommand_by_purpose_mutually_exclusive_with_other_rollups():
    with pytest.raises(SystemExit):
        _parse("tokens", "--org", "myorg", "--by-purpose", "--by-thread")


# ---- over_threshold(row, n) predicate: the single §7 would-alert seam ----


def test_over_threshold_is_strictly_greater_on_total_only():
    row = _rollup_row("thread_id", "THR-1", total=1000)
    # boundary: equal is NOT over (strict >)
    assert over_threshold(row, 1000) is False
    assert over_threshold(row, 999) is True
    assert over_threshold(row, 1001) is False


def test_over_threshold_ignores_cache_tokens():
    # cache_read is huge but churn (total) is tiny — must not trip the predicate
    row = _rollup_row("thread_id", "THR-1", total=10)
    row["cache_read_tokens"] = 5_000_000
    assert over_threshold(row, 100) is False


# ---- classify_model: every spec-§2 precedence case ----


def test_classify_model_single_id():
    row = {"model_distinct": 1, "model_any": "claude-opus-4-8",
           "non_null_sessions": 3, "null_codex_sessions": 0,
           "null_claude_sessions": 0,
           "null_claude_min_created_at": None, "null_claude_max_created_at": None}
    assert classify_model(row) == "claude-opus-4-8"


def test_classify_model_mixed_when_multiple_distinct():
    row = {"model_distinct": 2, "model_any": "claude-sonnet-4-6",
           "non_null_sessions": 2, "null_codex_sessions": 0,
           "null_claude_sessions": 0,
           "null_claude_min_created_at": None, "null_claude_max_created_at": None}
    assert classify_model(row) == "(mixed)"


def test_classify_model_mixed_when_nonnull_and_null_present():
    # one observed model + a NULL row -> (mixed)
    row = {"model_distinct": 1, "model_any": "gpt-5",
           "non_null_sessions": 1, "null_codex_sessions": 0,
           "null_claude_sessions": 1,
           "null_claude_min_created_at": "2026-06-12T10:00:00+00:00",
           "null_claude_max_created_at": "2026-06-12T10:00:00+00:00"}
    assert classify_model(row) == "(mixed)"


def test_classify_model_cli_unreported_for_codex_null():
    row = {"model_distinct": 0, "model_any": None,
           "non_null_sessions": 0, "null_codex_sessions": 4,
           "null_claude_sessions": 0,
           "null_claude_min_created_at": None, "null_claude_max_created_at": None}
    assert classify_model(row) == "(cli-unreported)"


def test_classify_model_pre_fix_for_claude_null_before_cutover():
    # all-NULL claude, max created_at strictly before the cutover constant
    row = {"model_distinct": 0, "model_any": None,
           "non_null_sessions": 0, "null_codex_sessions": 0,
           "null_claude_sessions": 2,
           "null_claude_min_created_at": "2026-06-12T10:00:00+00:00",
           "null_claude_max_created_at": "2026-06-12T11:00:00+00:00"}
    assert classify_model(row) == "(unknown — pre-fix)"


def test_classify_model_anomaly_for_claude_null_after_cutover():
    # all-NULL claude, a created_at at/after the cutover -> regression canary
    row = {"model_distinct": 0, "model_any": None,
           "non_null_sessions": 0, "null_codex_sessions": 0,
           "null_claude_sessions": 1,
           "null_claude_min_created_at": "2026-06-12T20:00:00+00:00",
           "null_claude_max_created_at": "2026-06-12T20:00:00+00:00"}
    assert classify_model(row) == "(unknown — ANOMALY)"


def test_classify_model_anomaly_boundary_uses_datetime_not_string():
    # Boundary guard: DB stamps '+00:00', the constant uses 'Z'. A naive
    # lexicographic compare ('+' < 'Z') would mislabel a same-instant row as
    # pre-fix. A row exactly AT the cutover instant (in +00:00 form) is >= and
    # must be ANOMALY.
    cutover_plus0000 = MODEL_FIX_CUTOVER_TS.replace("Z", "+00:00")
    row = {"model_distinct": 0, "model_any": None,
           "non_null_sessions": 0, "null_codex_sessions": 0,
           "null_claude_sessions": 1,
           "null_claude_min_created_at": cutover_plus0000,
           "null_claude_max_created_at": cutover_plus0000}
    assert classify_model(row) == "(unknown — ANOMALY)"


def test_classify_model_mixed_when_null_spans_codex_and_claude():
    row = {"model_distinct": 0, "model_any": None,
           "non_null_sessions": 0, "null_codex_sessions": 1,
           "null_claude_sessions": 1,
           "null_claude_min_created_at": "2026-06-12T10:00:00+00:00",
           "null_claude_max_created_at": "2026-06-12T10:00:00+00:00"}
    assert classify_model(row) == "(mixed)"


# ---- --by-purpose CLI routing + compose-with-filters ----


def test_cmd_tokens_by_purpose_routes_group_by_purpose():
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = []
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_purpose=True, thread_id="THR-015",
                              since="2026-06-06T00:00:00Z"))
    fake.aggregate_tokens.assert_called_once_with(
        slug="myorg", group_by="purpose",
        task_id=None, agent=None, since="2026-06-06T00:00:00Z", scope_type=None,
        scope_id=None, thread_id="THR-015", purpose=None,
    )
    fake.list_tokens.assert_not_called()


def test_cmd_tokens_by_purpose_has_no_model_column(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = [
        {"purpose": "thread-reply", "sessions": 14, "input_tokens": 980_300,
         "output_tokens": 70_114, "reasoning_tokens": 0,
         "cache_read_tokens": 11_200_400, "cache_creation_tokens": 0,
         "total_tokens": 1_050_414},
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_purpose=True))
    out = capsys.readouterr().out
    assert "Purpose" in out
    assert "thread-reply" in out
    assert "Model" not in out


# ---- --top: churn sort + slice + tie-break ----


def test_cmd_tokens_top_sorts_by_churn_desc_and_slices(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    # deliberately unsorted; cache is inverted vs churn to prove cache is ignored
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-low", total=100),
        _rollup_row("thread_id", "THR-high", total=9000),
        _rollup_row("thread_id", "THR-mid", total=500),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True, top=2))
    out = capsys.readouterr().out
    assert "THR-high" in out and "THR-mid" in out
    assert "THR-low" not in out          # sliced out by --top 2
    assert out.index("THR-high") < out.index("THR-mid")   # churn DESC


def test_cmd_tokens_top_tie_break_sessions_desc_then_key_asc(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    # all equal total; tie-break = sessions DESC, then key ASC
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-b", total=1000, sessions=2),
        _rollup_row("thread_id", "THR-a", total=1000, sessions=2),
        _rollup_row("thread_id", "THR-c", total=1000, sessions=9),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True, top=3))
    out = capsys.readouterr().out
    # THR-c first (most sessions); THR-a before THR-b (key ASC on the tie)
    assert out.index("THR-c") < out.index("THR-a") < out.index("THR-b")


def test_cmd_tokens_top_requires_a_rollup_flag():
    from cli.main import cmd_tokens

    fake = MagicMock()
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        with pytest.raises(SystemExit):
            cmd_tokens(_mock_args(top=5))   # no --by-* flag
    fake.aggregate_tokens.assert_not_called()
    fake.list_tokens.assert_not_called()


def test_cmd_tokens_top_json_emits_sorted_sliced_list(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-low", total=100),
        _rollup_row("thread_id", "THR-high", total=9000),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True, top=1, json=True))
    parsed = _json.loads(capsys.readouterr().out)
    assert [r["thread_id"] for r in parsed] == ["THR-high"]


# ---- --over-threshold: passive predicate + compose with --top ----


def test_cmd_tokens_over_threshold_keeps_only_groups_above(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-big", total=2_000_000),
        _rollup_row("thread_id", "THR-small", total=10),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True, over_threshold=1_000_000))
    out = capsys.readouterr().out
    assert "THR-big" in out
    assert "THR-small" not in out


def test_cmd_tokens_over_threshold_applies_before_top(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    # THR-mid would survive --top 1 by churn, but is BELOW threshold and must
    # be filtered out FIRST, leaving only THR-big.
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-big", total=2_000_000),
        _rollup_row("thread_id", "THR-mid", total=500),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True, over_threshold=1_000_000, top=5))
    out = capsys.readouterr().out
    assert "THR-big" in out
    assert "THR-mid" not in out


def test_cmd_tokens_over_threshold_empty_prints_nothing_would_alert(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-small", total=10),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True, over_threshold=1_000_000))
    out = capsys.readouterr().out
    assert "THR-small" not in out
    assert "over" in out.lower()    # "No thread over 1,000,000 tokens in window."


def test_cmd_tokens_over_threshold_requires_a_rollup_flag():
    from cli.main import cmd_tokens

    fake = MagicMock()
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        with pytest.raises(SystemExit):
            cmd_tokens(_mock_args(over_threshold=100))   # no --by-* flag
    fake.aggregate_tokens.assert_not_called()


# ---- Model column on by-agent/by-thread ----


def test_cmd_tokens_by_thread_renders_model_column(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-015", total=1000,
                    model_distinct=1, model_any="claude-opus-4-8[1m]",
                    non_null_sessions=3, null_codex_sessions=0,
                    null_claude_sessions=0,
                    null_claude_min_created_at=None,
                    null_claude_max_created_at=None),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True))
    out = capsys.readouterr().out
    assert "Model" in out
    assert "claude-opus-4-8[1m]" in out


def test_cmd_tokens_by_thread_renders_cli_unreported_label(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = [
        _rollup_row("thread_id", "THR-codex", total=1000,
                    model_distinct=0, model_any=None,
                    non_null_sessions=0, null_codex_sessions=4,
                    null_claude_sessions=0,
                    null_claude_min_created_at=None,
                    null_claude_max_created_at=None),
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_thread=True))
    out = capsys.readouterr().out
    assert "(cli-unreported)" in out


def test_cmd_tokens_by_task_has_no_model_column(capsys):
    from cli.main import cmd_tokens

    fake = MagicMock()
    fake.aggregate_tokens.return_value = [
        {"task_id": "TASK-1", "sessions": 1, "input_tokens": 10,
         "output_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0,
         "cache_creation_tokens": 0, "total_tokens": 10},
    ]
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["myorg"]):
        cmd_tokens(_mock_args(by_task=True))
    out = capsys.readouterr().out
    assert "Task" in out
    assert "Model" not in out
