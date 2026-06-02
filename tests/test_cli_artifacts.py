"""Tests for the ``happyranch artifacts`` CLI subcommand.

Mirrors the argparse-only + ``cmd_*`` mock pattern used in
``tests/test_cli_tokens.py`` — we exercise parser wiring directly and stub
out :class:`OpcClient` for the command-handler tests.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.cli import build_parser


def _parse(*args: str) -> argparse.Namespace:
    return build_parser().parse_args(list(args))


# ── argparse parsing ─────────────────────────────────────────────────────────


def test_artifacts_put_parses_required_args(tmp_path: Path) -> None:
    local = tmp_path / "report.pdf"
    local.write_bytes(b"hello")
    ns = _parse("artifacts", "put", str(local), "--agent", "dev_agent", "--org", "demo")
    assert ns.command == "artifacts"
    assert ns.artifacts_cmd == "put"
    assert ns.local_path == local
    assert ns.agent == "dev_agent"
    assert ns.org == "demo"
    assert ns.name is None


def test_artifacts_put_parses_name_override(tmp_path: Path) -> None:
    local = tmp_path / "report.pdf"
    local.write_bytes(b"x")
    ns = _parse(
        "artifacts", "put", str(local),
        "--agent", "dev_agent",
        "--name", "renamed.pdf",
        "--org", "demo",
    )
    assert ns.name == "renamed.pdf"


def test_artifacts_list_parses(tmp_path: Path) -> None:
    ns = _parse("artifacts", "list", "--org", "demo")
    assert ns.command == "artifacts"
    assert ns.artifacts_cmd == "list"
    assert ns.org == "demo"


def test_artifacts_get_parses(tmp_path: Path) -> None:
    out = tmp_path / "out.bin"
    ns = _parse("artifacts", "get", "a.txt", "--output", str(out), "--org", "demo")
    assert ns.command == "artifacts"
    assert ns.artifacts_cmd == "get"
    assert ns.name == "a.txt"
    assert ns.output == out
    assert ns.org == "demo"


# ── cmd_artifacts_put ────────────────────────────────────────────────────────


def test_cmd_artifacts_put_invokes_client(tmp_path: Path, capsys) -> None:
    from src.cli import cmd_artifacts_put

    local = tmp_path / "report.pdf"
    local.write_bytes(b"hello")

    fake = MagicMock()
    fake.put_artifact.return_value = {
        "name": "report.pdf",
        "size_bytes": 5,
        "modified_at": "2026-05-27T00:00:00Z",
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            local_path=local,
            name=None,
            agent="dev_agent",
        )
        cmd_artifacts_put(args)

    fake.put_artifact.assert_called_once_with(
        slug="demo",
        local_path=local,
        name=None,
        agent="dev_agent",
    )
    out = capsys.readouterr().out
    assert "report.pdf" in out
    assert "5" in out


def test_cmd_artifacts_put_with_name_override(tmp_path: Path) -> None:
    from src.cli import cmd_artifacts_put

    local = tmp_path / "report.pdf"
    local.write_bytes(b"data")

    fake = MagicMock()
    fake.put_artifact.return_value = {
        "name": "renamed.pdf",
        "size_bytes": 4,
        "modified_at": "2026-05-27T00:00:00Z",
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(
            org="demo",
            local_path=local,
            name="renamed.pdf",
            agent="dev_agent",
        )
        cmd_artifacts_put(args)

    fake.put_artifact.assert_called_once_with(
        slug="demo",
        local_path=local,
        name="renamed.pdf",
        agent="dev_agent",
    )


# ── cmd_artifacts_list ───────────────────────────────────────────────────────


def test_cmd_artifacts_list_invokes_client(capsys) -> None:
    from src.cli import cmd_artifacts_list

    fake = MagicMock()
    fake.list_artifacts.return_value = {
        "artifacts": [
            {"name": "a.txt", "size_bytes": 1, "modified_at": "2026-05-27T00:00:00Z"},
            {"name": "b.bin", "size_bytes": 99, "modified_at": "2026-05-27T01:00:00Z"},
        ]
    }

    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(org="demo")
        cmd_artifacts_list(args)

    fake.list_artifacts.assert_called_once_with(slug="demo")
    out = capsys.readouterr().out
    assert "a.txt" in out
    assert "b.bin" in out


def test_cmd_artifacts_list_empty(capsys) -> None:
    from src.cli import cmd_artifacts_list

    fake = MagicMock()
    fake.list_artifacts.return_value = {"artifacts": []}

    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(org="demo")
        cmd_artifacts_list(args)

    out = capsys.readouterr().out
    assert out.strip() == "no artifacts"


# ── cmd_artifacts_get ────────────────────────────────────────────────────────


def test_cmd_artifacts_get_writes_to_output(tmp_path: Path, capsys) -> None:
    from src.cli import cmd_artifacts_get

    out = tmp_path / "downloaded.bin"

    fake = MagicMock()
    fake.get_artifact.return_value = b"contents"

    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli._fetch_available_orgs", return_value=["demo"]):
        args = argparse.Namespace(org="demo", name="a.txt", output=out)
        cmd_artifacts_get(args)

    fake.get_artifact.assert_called_once_with(slug="demo", name="a.txt")
    assert out.read_bytes() == b"contents"
    printed = capsys.readouterr().out
    assert str(out) in printed
