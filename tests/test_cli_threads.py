from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
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


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc)


def _stub_client(monkeypatch, fake: Mock) -> None:
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: fake)
    monkeypatch.setattr(
        "cli.commands.threads._shared._fetch_available_orgs",
        lambda _client: ["alpha"],
    )
    monkeypatch.setattr("cli.commands.threads.datetime", _FixedDateTime, raising=False)


def test_threads_parser_accepts_repeated_attach_flags(tmp_path: Path) -> None:
    from cli.main import build_parser

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"

    ns = build_parser().parse_args([
        "threads",
        "reply",
        "--org",
        "alpha",
        "--thread-id",
        "THR-001",
        "--from-file",
        str(tmp_path / "reply.json"),
        "--attach",
        str(a),
        "--attach",
        str(b),
    ])

    assert ns.attach == [a, b]


def test_threads_parser_attach_defaults_to_none(tmp_path: Path) -> None:
    from cli.main import build_parser

    ns = build_parser().parse_args([
        "threads",
        "reply",
        "--org",
        "alpha",
        "--thread-id",
        "THR-001",
        "--from-file",
        str(tmp_path / "reply.json"),
    ])

    assert ns.attach is None


def test_threads_send_attach_uploads_and_merges_refs(tmp_path: Path, monkeypatch) -> None:
    from cli.main import cmd_threads_send

    payload_path = tmp_path / "msg.json"
    payload_path.write_text(
        json.dumps({
            "body_markdown": "see attached",
            "attachments": [
                {"artifact_name": "existing.pdf", "display_name": "existing.pdf"},
            ],
        }),
        encoding="utf-8",
    )
    local = tmp_path / "report.pdf"
    local.write_bytes(b"pdf")
    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "THR-001-20260609T000000Z-report.pdf",
        "size_bytes": 3,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=payload_path,
        attach=[local],
    )

    cmd_threads_send(args)

    fake.put_artifact.assert_called_once()
    assert fake.put_artifact.call_args.kwargs["agent"] == "founder"
    assert fake.put_artifact.call_args.kwargs["name"] == "THR-001-20260609T000000Z-report.pdf"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["body_markdown"] == "see attached"
    assert sent["attachments"] == [
        {"artifact_name": "existing.pdf", "display_name": "existing.pdf"},
        {
            "artifact_name": "THR-001-20260609T000000Z-report.pdf",
            "display_name": "report.pdf",
            "content_type": "application/pdf",
        },
    ]


def test_threads_send_attach_disambiguates_duplicate_generated_names(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from cli.main import cmd_threads_send

    payload_path = tmp_path / "msg.json"
    payload_path.write_text(json.dumps({"body_markdown": ""}), encoding="utf-8")
    local = tmp_path / "report.pdf"
    local.write_bytes(b"pdf")
    fake = Mock()
    fake.put_artifact.side_effect = lambda **kwargs: {
        "name": kwargs["name"],
        "size_bytes": 3,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=payload_path,
        attach=[local, local],
    )

    cmd_threads_send(args)

    names = [call.kwargs["name"] for call in fake.put_artifact.call_args_list]
    assert names == [
        "THR-001-20260609T000000Z-report.pdf",
        "THR-001-20260609T000000Z-2-report.pdf",
    ]
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {
            "artifact_name": "THR-001-20260609T000000Z-report.pdf",
            "display_name": "report.pdf",
            "content_type": "application/pdf",
        },
        {
            "artifact_name": "THR-001-20260609T000000Z-2-report.pdf",
            "display_name": "report.pdf",
            "content_type": "application/pdf",
        },
    ]


def test_threads_reply_attach_uses_speaker_for_upload_attribution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from cli.main import cmd_threads_reply

    payload_path = tmp_path / "reply.json"
    payload_path.write_text(
        json.dumps({
            "thread_id": "THR-001",
            "invocation_token": "tok",
            "speaker": "dev_agent",
            "body_markdown": "",
            "in_response_to_seq": 1,
        }),
        encoding="utf-8",
    )
    local = tmp_path / "analysis.md"
    local.write_text("analysis", encoding="utf-8")
    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "THR-001-20260609T000000Z-analysis.md",
        "size_bytes": 8,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=payload_path,
        attach=[local],
    )

    cmd_threads_reply(args)

    assert fake.put_artifact.call_args.kwargs["agent"] == "dev_agent"
    assert fake.put_artifact.call_args.kwargs["name"] == "THR-001-20260609T000000Z-analysis.md"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {
            "artifact_name": "THR-001-20260609T000000Z-analysis.md",
            "display_name": "analysis.md",
            "content_type": "text/markdown",
        },
    ]


def test_threads_compose_attach_uploads_with_founder_attribution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from cli.main import cmd_threads_compose

    local = tmp_path / "data.csv"
    local.write_text("a,b\n", encoding="utf-8")
    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "thread-draft-20260609T000000Z-data.csv",
        "size_bytes": 4,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response(
        {
            "thread_id": "THR-001",
            "started_at": "2026-06-09T00:00:00Z",
            "pending_replies": [],
        }
    )
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        task_id=None,
        session_id=None,
        from_file=None,
        subject="Review data",
        recipients="dev_agent",
        body="",
        attach=[local],
    )

    cmd_threads_compose(args)

    assert fake.put_artifact.call_args.kwargs["agent"] == "founder"
    assert fake.put_artifact.call_args.kwargs["name"] == "thread-draft-20260609T000000Z-data.csv"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {
            "artifact_name": "thread-draft-20260609T000000Z-data.csv",
            "display_name": "data.csv",
            "content_type": "text/csv",
        },
    ]


def test_threads_compose_as_agent_attach_uses_composer_attribution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from cli.main import cmd_threads_compose

    payload_path = tmp_path / "compose.json"
    payload_path.write_text(
        json.dumps({
            "composer": "dev_agent",
            "subject": "Files",
            "recipients": ["review_agent"],
            "body_markdown": "see attached",
        }),
        encoding="utf-8",
    )
    local = tmp_path / "notes.md"
    local.write_text("notes", encoding="utf-8")
    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "thread-draft-20260609T000000Z-notes.md",
        "size_bytes": 5,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response(
        {
            "thread_id": "THR-001",
            "started_at": "2026-06-09T00:00:00Z",
            "composed_by": "dev_agent",
            "pending_replies": [],
        }
    )
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        task_id="TASK-001",
        session_id="sess-1",
        from_file=payload_path,
        subject=None,
        recipients=None,
        body=None,
        attach=[local],
    )

    cmd_threads_compose(args)

    assert fake.put_artifact.call_args.kwargs["agent"] == "dev_agent"
    assert fake.put_artifact.call_args.kwargs["name"] == "thread-draft-20260609T000000Z-notes.md"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {
            "artifact_name": "thread-draft-20260609T000000Z-notes.md",
            "display_name": "notes.md",
            "content_type": "text/markdown",
        },
    ]
