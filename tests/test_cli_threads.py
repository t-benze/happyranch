from __future__ import annotations

import argparse
from unittest.mock import Mock


def _json_response(body: dict) -> Mock:
    response = Mock()
    response.status_code = 200
    response.json.return_value = body
    return response


def test_threads_show_prints_attachments(monkeypatch, capsys) -> None:
    from cli.main import cmd_threads_show

    fake = Mock()
    fake.get.return_value = _json_response({
        "thread_id": "THR-001",
        "subject": "Files",
        "status": "open",
        "turns_used": 1,
        "turn_cap": 500,
        "participants": ["dev_agent"],
        "forwarded_from_id": None,
        "messages": [
            {
                "seq": 1,
                "speaker": "founder",
                "kind": "message",
                "body_markdown": None,
                "decline_reason": None,
                "system_payload": None,
                "attachments": [
                    {
                        "artifact_name": "THR-001-report.pdf",
                        "display_name": "report.pdf",
                        "size_bytes": 123,
                        "content_type": None,
                        "uploaded_by": "founder",
                    }
                ],
                "created_at": "2026-06-09T00:00:00Z",
                "responder_status": [],
            }
        ],
    })
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: fake)
    monkeypatch.setattr(
        "cli.commands.threads._shared._fetch_available_orgs",
        lambda _client: ["alpha"],
    )

    cmd_threads_show(argparse.Namespace(org="alpha", thread_id="THR-001", json=False))

    out = capsys.readouterr().out
    assert "attachment: report.pdf [artifact:THR-001-report.pdf] (123B)" in out
