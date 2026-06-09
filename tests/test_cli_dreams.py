from __future__ import annotations

import argparse
import json

from cli.commands import dreams


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeClient:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, path, json=None, params=None):
        self.posts.append((path, json, params))
        return FakeResponse({"dream_id": "DREAM-001", "status": "completed"})

    def get(self, path, params=None):
        self.gets.append((path, params))
        return FakeResponse({"dreams": []})


def test_expand_complete_payload_reads_kb_candidate_body(tmp_path):
    body_path = tmp_path / "candidate.md"
    body_path.write_text("Candidate body.\n")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({
        "summary": "s",
        "learnings": [],
        "kb_candidates": [{
            "slug": "candidate",
            "title": "Candidate",
            "topic": "workflow",
            "rationale": "Repeated pattern.",
            "body_path": str(body_path),
        }],
        "founder_thread": {"needed": False},
    }))

    expanded = dreams._complete_payload_from_file(str(payload_path))

    assert expanded["kb_candidates"][0]["body_markdown"] == "Candidate body.\n"
    assert "body_path" not in expanded["kb_candidates"][0]


def test_cmd_dreams_complete_posts_expanded_payload(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(dreams.OpcClient, "from_env", lambda: client)
    body_path = tmp_path / "candidate.md"
    body_path.write_text("Candidate body.\n")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({
        "summary": "s",
        "learnings": [],
        "kb_candidates": [{
            "slug": "candidate",
            "title": "Candidate",
            "topic": "workflow",
            "rationale": "Repeated pattern.",
            "body_path": str(body_path),
        }],
        "founder_thread": {"needed": False},
    }))

    dreams.cmd_dreams_complete(argparse.Namespace(
        org="myorg", dream_id="DREAM-001", from_file=str(payload_path),
    ))

    assert client.posts[0][0] == "/api/v1/orgs/myorg/dreams/DREAM-001/complete"
    assert client.posts[0][1]["kb_candidates"][0]["body_markdown"] == "Candidate body.\n"


def test_dreams_command_registered():
    from cli.main import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "dreams" in sub.choices
