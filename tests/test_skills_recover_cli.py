"""CLI contract tests for B2 canonical-package recovery."""
from __future__ import annotations

import argparse

import pytest


def test_skills_recover_posts_confirmed_b2_provenance(monkeypatch, tmp_path, capsys):
    from cli.commands import skills

    port_path = tmp_path / "daemon.port"
    port_path.write_text("8765")
    (tmp_path / "daemon.token").write_text("token")
    captured: dict = {}

    class Response:
        status_code = 200

        def json(self):
            return {"orgs": [{"slug": "alpha"}]} if not captured.get("posted") else {"message": "recovered"}

    class Client:
        def __init__(self, **kwargs):
            captured["headers"] = kwargs.get("headers")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, path):
            assert path == "/api/v1/orgs"
            return Response()

        def post(self, path, json):
            captured.update(posted=True, path=path, json=json)
            return Response()

    import cli._shared
    import cli.client.client
    import httpx

    monkeypatch.setattr(cli.client.client, "port_file", lambda: port_path)
    monkeypatch.setattr(cli._shared, "resolve_org_slug", lambda **_kwargs: "alpha")
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    content_hash = "a" * 64
    skills.cmd_skills_recover(argparse.Namespace(slug="b2-skill", version="7", content_hash=content_hash, org=None))

    assert captured["path"] == "/api/v1/orgs/alpha/skills/recover"
    assert captured["json"] == {"slug": "b2-skill", "version": "7", "content_hash": content_hash}
    assert "recovered" in capsys.readouterr().out


def test_skills_help_retains_recover(capsys):
    import argparse

    from cli.commands import skills

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    skills.register(subparsers)
    with pytest.raises(SystemExit) as exit_info:
        parser.parse_args(["skills", "recover", "--help"])
    assert exit_info.value.code == 0
    assert "recover" in capsys.readouterr().out
