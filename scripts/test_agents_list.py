"""Focused roster CLI checks outside the retired Python unit suite.

Run: uv run --frozen pytest scripts/test_agents_list.py -v --tb=short
"""
from __future__ import annotations

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from cli.commands import agents
from cli.main import build_parser


def invoke(argv: list[str], response: httpx.Response) -> Mock:
    client = Mock()
    client.get.side_effect = [
        httpx.Response(200, json={"orgs": [{"slug": "alpha"}]}), response,
    ]
    with patch.object(agents.OpcClient, "from_env", return_value=client):
        args = build_parser().parse_args(["agents", "list", *argv])
        args.func(args)
    assert client.get.call_args_list == [
        (("/api/v1/orgs",), {}), (("/api/v1/orgs/alpha/agents",), {}),
    ]
    return client


def test_agents_list_json_preserves_response_rows(capsys: pytest.CaptureFixture[str]) -> None:
    rows = [{
        "name": "writer2", "team": "content", "role": "worker", "executor": "claude",
        "model": None, "reasoning_effort": "high", "description": "Writes guides 旅游",
        "repos": {"guides": "https://example.test/guides.git"}, "revision": "a" * 64,
        "system_prompt": "Synthetic prompt", "addressable_name": "Writer-2",
        "name_revision": 7, "naming_status": "available", "future_field": {"kept": True},
    }]
    invoke(["--org", "alpha", "--json"], httpx.Response(200, json={"agents": rows}))
    output = json.loads(capsys.readouterr().out)
    assert output == rows
    assert "allow_rules" not in output[0]


def test_agents_list_text_one_line_per_agent(capsys: pytest.CaptureFixture[str],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAPPYRANCH_ORG_SLUG", raising=False)
    invoke([], httpx.Response(200, json={"agents": [
        {"name": "writer2", "team": "content", "role": "worker", "revision": "b" * 64,
         "repos": {"guides": "https://example.test/guides.git"}},
        {"name": "manager", "team": "engineering", "role": "manager", "revision": "c" * 64,
         "repos": {}},
    ]}))
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert lines[0] == ('writer2  team=content  role=worker  revision=' + 'b' * 64
                        + '  repos={"guides": "https://example.test/guides.git"}')
    assert lines[1] == 'manager  team=engineering  role=manager  revision=' + 'c' * 64 + '  repos={}'


@pytest.mark.parametrize("mode", [[], ["--json"]], ids=["text", "json"])
def test_agents_list_not_ok_exits_without_roster(mode: list[str],
                                                capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        invoke(["--org", "alpha", *mode], httpx.Response(403, json={"detail": "synthetic refusal"}))
    assert error.value.code == 1
    assert capsys.readouterr().out.strip() == 'Error (403): {"detail":"synthetic refusal"}'
