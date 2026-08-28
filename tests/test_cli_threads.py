from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest


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
    # Thread-scoped upload (default, TASK-1616).
    fake.upload_thread_attachment.return_value = {
        "attachment_id": "att-001",
        "display_name": "report.pdf",
        "size_bytes": 3,
    }
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

    # Default path: thread-scoped upload (since thread_id is set).
    fake.upload_thread_attachment.assert_called_once()
    fake.put_artifact.assert_not_called()
    sent = fake.post.call_args.kwargs["json"]
    assert sent["body_markdown"] == "see attached"
    assert sent["attachments"] == [
        {"artifact_name": "existing.pdf", "display_name": "existing.pdf"},
        {
            "attachment_id": "att-001",
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
    # Thread-scoped uploads each get a unique auto-generated attachment_id.
    fake.upload_thread_attachment.side_effect = [
        {"attachment_id": "att-001", "display_name": "report.pdf", "size_bytes": 3},
        {"attachment_id": "att-002", "display_name": "report.pdf", "size_bytes": 3},
    ]
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=payload_path,
        attach=[local, local],
    )

    cmd_threads_send(args)

    # Thread-scoped uploads are called twice, each returns a unique attachment_id.
    assert fake.upload_thread_attachment.call_count == 2
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {
            "attachment_id": "att-001",
            "display_name": "report.pdf",
            "content_type": "application/pdf",
        },
        {
            "attachment_id": "att-002",
            "display_name": "report.pdf",
            "content_type": "application/pdf",
        },
    ]


def test_threads_send_with_task_id_passes_binding_to_send_route(
    tmp_path: Path, monkeypatch
) -> None:
    """`threads send --task-id T --session-id S` => POSTs composer/task_id/session_id in the send body."""
    from cli.main import cmd_threads_send

    payload_path = tmp_path / "msg.json"
    payload_path.write_text(
        json.dumps({
            "composer": "dev_agent",
            "body_markdown": "agent message",
        }),
        encoding="utf-8",
    )
    fake = Mock()
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=str(payload_path),
        task_id="TASK-200",
        session_id="sess-200",
        attach=[],
    )

    cmd_threads_send(args)

    fake.post.assert_called_once()
    sent = fake.post.call_args.kwargs["json"]
    # Binding fields are present in the POST body.
    assert sent["composer"] == "dev_agent"
    assert sent["task_id"] == "TASK-200"
    assert sent["session_id"] == "sess-200"
    assert sent["body_markdown"] == "agent message"
    # Route is the same /send endpoint (binding is in-body).
    assert "/send" in fake.post.call_args.args[0]


def test_threads_send_without_task_id_omits_binding(
    tmp_path: Path, monkeypatch
) -> None:
    """Plain `threads send` (no --task-id) => no composer/task_id/session_id in the POST body."""
    from cli.main import cmd_threads_send

    payload_path = tmp_path / "msg.json"
    payload_path.write_text(
        json.dumps({"body_markdown": "founder follow-up"}),
        encoding="utf-8",
    )
    fake = Mock()
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=str(payload_path),
        task_id=None,
        session_id=None,
        attach=[],
    )

    cmd_threads_send(args)

    fake.post.assert_called_once()
    sent = fake.post.call_args.kwargs["json"]
    # No binding fields in the founder path.
    assert "composer" not in sent
    # body_markdown still present.
    assert sent["body_markdown"] == "founder follow-up"


def test_threads_send_task_id_without_session_id_exits_early(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """FINDING 1: `threads send --task-id T` without --session-id => fail fast, never POST."""
    import sys
    from cli.main import cmd_threads_send

    payload_path = tmp_path / "msg.json"
    payload_path.write_text(
        json.dumps({
            "composer": "dev_agent",
            "body_markdown": "agent message",
        }),
        encoding="utf-8",
    )
    fake = Mock()
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=str(payload_path),
        task_id="TASK-200",
        session_id=None,  # missing!
        attach=[],
    )

    with pytest.raises(SystemExit) as exc_info:
        cmd_threads_send(args)

    assert exc_info.value.code == 2
    fake.post.assert_not_called()


def test_threads_send_session_id_without_task_id_exits_early(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """FINDING 1: `threads send --session-id S` without --task-id => fail fast."""
    import sys
    from cli.main import cmd_threads_send

    payload_path = tmp_path / "msg.json"
    payload_path.write_text(
        json.dumps({
            "composer": "dev_agent",
            "body_markdown": "agent message",
        }),
        encoding="utf-8",
    )
    fake = Mock()
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=str(payload_path),
        task_id=None,  # missing!
        session_id="sess-200",
        attach=[],
    )

    with pytest.raises(SystemExit) as exc_info:
        cmd_threads_send(args)

    assert exc_info.value.code == 2
    fake.post.assert_not_called()


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
    fake.upload_thread_attachment.return_value = {
        "attachment_id": "att-001",
        "display_name": "analysis.md",
        "size_bytes": 8,
    }
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

    # Thread-scoped upload (reply has thread_id).
    assert fake.upload_thread_attachment.call_args.kwargs["agent"] == "dev_agent"
    assert fake.upload_thread_attachment.call_args.kwargs["thread_id"] == "THR-001"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {
            "attachment_id": "att-001",
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
    fake.put_artifact = Mock()
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

    # Compose with --attach uses thread-scoped multipart (TASK-1616).
    # put_artifact (shared artifacts) is NOT called.
    fake.put_artifact.assert_not_called()
    # POST uses multipart form data with body + files fields.
    call_kwargs = fake.post.call_args.kwargs
    assert "files" in call_kwargs
    assert "data" in call_kwargs
    assert "body" in call_kwargs["data"]
    body_json = json.loads(call_kwargs["data"]["body"])
    assert body_json["subject"] == "Review data"
    assert body_json["recipients"] == ["dev_agent"]


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
    fake.put_artifact = Mock()
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

    # Compose-as-agent with --attach uses thread-scoped multipart (TASK-1616).
    # put_artifact (shared artifacts) is NOT called.
    fake.put_artifact.assert_not_called()
    # POST uses multipart form data with body + files fields.
    call_kwargs = fake.post.call_args.kwargs
    assert "files" in call_kwargs
    assert "data" in call_kwargs
    assert "body" in call_kwargs["data"]
    body_json = json.loads(call_kwargs["data"]["body"])
    assert body_json["composer"] == "dev_agent"
    assert body_json["task_id"] == "TASK-001"
    assert body_json["session_id"] == "sess-1"


def test_threads_dispatch_prints_superseded_task_id(tmp_path: Path, monkeypatch, capsys) -> None:
    """When the dispatch response includes superseded_task_id, the CLI prints it."""
    from cli.commands.threads import cmd_threads_dispatch

    payload_path = tmp_path / "dispatch.json"
    payload_path.write_text(
        json.dumps({
            "thread_id": "THR-001",
            "invocation_token": "tok",
            "dispatcher": "engineering_head",
            "brief": "continue",
            "resolves": "TASK-900",
        }),
        encoding="utf-8",
    )
    fake = Mock()
    fake.post.return_value = _json_response({
        "task_id": "TASK-999",
        "dispatched_from_thread_id": "THR-001",
        "superseded_task_id": "TASK-900",
    })
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(org="alpha", thread_id="THR-001", from_file=payload_path)
    cmd_threads_dispatch(args)

    out = capsys.readouterr().out
    assert "ok: dispatched TASK-999 from THR-001 -> supersedes TASK-900" in out


def test_threads_dispatch_no_supersede_prints_plain(tmp_path: Path, monkeypatch, capsys) -> None:
    """When no superseded_task_id, the CLI prints the existing plain message."""
    from cli.commands.threads import cmd_threads_dispatch

    payload_path = tmp_path / "dispatch.json"
    payload_path.write_text(
        json.dumps({
            "thread_id": "THR-001",
            "invocation_token": "tok",
            "dispatcher": "engineering_head",
            "brief": "create new task",
        }),
        encoding="utf-8",
    )
    fake = Mock()
    fake.post.return_value = _json_response({
        "task_id": "TASK-888",
        "dispatched_from_thread_id": "THR-001",
        "superseded_task_id": None,
    })
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(org="alpha", thread_id="THR-001", from_file=payload_path)
    cmd_threads_dispatch(args)

    out = capsys.readouterr().out
    assert "ok: dispatched TASK-888 from THR-001" in out
    assert "supersedes" not in out


def test_threads_abort_replies_prints_json(monkeypatch, capsys) -> None:
    """abort-replies prints JSON result like other founder thread actions."""
    from cli.commands.threads import cmd_threads_abort_replies

    fake = Mock()
    fake.post.return_value = _json_response({
        "thread_id": "THR-001",
        "aborted_count": 2,
    })
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(org="alpha", thread_id="THR-001")
    cmd_threads_abort_replies(args)

    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["thread_id"] == "THR-001"
    assert result["aborted_count"] == 2


# ── CLI attachments list/get tests (TASK-1616) ─────────────────────────────


def test_threads_attachments_list_prints_rows(monkeypatch, capsys) -> None:
    from cli.commands.threads import cmd_threads_attachments_list

    fake = Mock()
    fake.list_thread_attachments.return_value = {
        "attachments": [
            {
                "attachment_id": "att-001",
                "display_name": "data.csv",
                "size_bytes": 100,
                "content_type": "text/csv",
            },
            {
                "attachment_id": "att-002",
                "display_name": "notes.md",
                "size_bytes": 50,
                "content_type": "text/markdown",
            },
        ]
    }
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", from_file=None,
        agent="founder", invocation_token=None,
    )
    cmd_threads_attachments_list(args)

    out = capsys.readouterr().out
    assert "att-001" in out
    assert "data.csv" in out
    assert "100B" in out
    assert "att-002" in out
    assert "notes.md" in out


def test_threads_attachments_list_empty(monkeypatch, capsys) -> None:
    from cli.commands.threads import cmd_threads_attachments_list

    fake = Mock()
    fake.list_thread_attachments.return_value = {"attachments": []}
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", from_file=None,
        agent="founder", invocation_token=None,
    )
    cmd_threads_attachments_list(args)

    out = capsys.readouterr().out
    assert "no thread-scoped attachments" in out


def test_threads_attachments_get_saves_file(monkeypatch, capsys, tmp_path: Path) -> None:
    from cli.commands.threads import cmd_threads_attachments_get

    fake = Mock()
    fake.get_thread_attachment.return_value = b"hello world"
    _stub_client(monkeypatch, fake)

    out_path = tmp_path / "downloaded.txt"
    args = argparse.Namespace(
        org="alpha", thread_id="THR-001",
        attachment_id="att-001",
        output=str(out_path),
        from_file=None,
        agent="founder", invocation_token=None,
    )
    cmd_threads_attachments_get(args)

    out = capsys.readouterr().out
    assert "11B" in out
    assert out_path.read_bytes() == b"hello world"


def test_threads_attachments_parser_list(monkeypatch) -> None:
    """Parser accepts 'threads attachments list --thread-id X'."""
    from cli.main import build_parser
    p = build_parser()
    ns = p.parse_args(["threads", "attachments", "list", "--org", "alpha", "--thread-id", "THR-001"])
    assert ns.func is not None
    assert ns.thread_id == "THR-001"


def test_threads_attachments_parser_get(monkeypatch) -> None:
    """Parser accepts 'threads attachments get --thread-id X ATT_ID -o out'."""
    from cli.main import build_parser
    p = build_parser()
    ns = p.parse_args([
        "threads", "attachments", "get",
        "--org", "alpha", "--thread-id", "THR-001",
        "att-001", "-o", "/tmp/out.txt",
    ])
    assert ns.func is not None
    assert ns.attachment_id == "att-001"
    assert ns.output == "/tmp/out.txt"


# ── CLI --shared flag (escape hatch) tests ─────────────────────────────────


def test_threads_reply_shared_uses_artifact(monkeypatch, tmp_path: Path) -> None:
    """reply --shared uses shared artifacts instead of thread-scoped."""
    from cli.commands.threads import cmd_threads_reply
    payload_path = tmp_path / "reply.json"
    payload_path.write_text(json.dumps({
        "thread_id": "THR-001",
        "invocation_token": "tok",
        "speaker": "dev_agent",
        "body_markdown": "hi",
        "in_response_to_seq": 1,
    }))
    local = tmp_path / "report.pdf"
    local.write_text("report", encoding="utf-8")

    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "report-shared.pdf", "size_bytes": 6,
        "modified_at": "2026-01-01T00:00:00Z",
    }
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2, "kind": "message"})
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id="THR-001",
        from_file=str(payload_path), attach=[local],
        shared=True,
    )
    cmd_threads_reply(args)

    # put_artifact (shared) was called, not upload_thread_attachment.
    fake.put_artifact.assert_called_once()


def test_threads_compose_shared_uses_artifact(monkeypatch, tmp_path: Path) -> None:
    """compose --shared --attach uses shared artifacts."""
    from cli.main import cmd_threads_compose
    local = tmp_path / "notes.md"
    local.write_text("notes", encoding="utf-8")

    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "shared-notes.md", "size_bytes": 5,
        "modified_at": "2026-01-01T00:00:00Z",
    }
    fake.post.return_value = _json_response({
        "thread_id": "THR-001", "started_at": "2026-01-01T00:00:00Z",
        "pending_replies": [],
    })
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", task_id=None, session_id=None, from_file=None,
        subject="Review", recipients="dev_agent", body="",
        attach=[local], shared=True,
    )
    cmd_threads_compose(args)

    # put_artifact (shared) was called, not multipart.
    fake.put_artifact.assert_called_once()


def test_threads_attachments_list_passes_agent_and_token(
    monkeypatch, capsys,
) -> None:
    """attachments list passes agent + invocation_token to the client."""
    from cli.commands.threads import cmd_threads_attachments_list
    fake = Mock()
    fake.list_thread_attachments.return_value = {"attachments": []}
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", from_file=None,
        agent="dev_agent", invocation_token="tok-abc",
    )
    cmd_threads_attachments_list(args)

    assert fake.list_thread_attachments.call_args.kwargs["agent"] == "dev_agent"
    assert fake.list_thread_attachments.call_args.kwargs["invocation_token"] == "tok-abc"
    assert fake.list_thread_attachments.call_args.kwargs["thread_id"] == "THR-001"


def test_threads_attachments_list_from_file(
    monkeypatch, capsys, tmp_path: Path,
) -> None:
    """attachments list --from-file loads agent + token from JSON."""
    from cli.commands.threads import cmd_threads_attachments_list
    payload_path = tmp_path / "proof.json"
    payload_path.write_text(json.dumps({
        "thread_id": "THR-001",
        "agent": "dev_agent",
        "invocation_token": "tok-xyz",
    }))
    fake = Mock()
    fake.list_thread_attachments.return_value = {"attachments": []}
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id=None, from_file=str(payload_path),
        agent=None, invocation_token=None,
    )
    cmd_threads_attachments_list(args)

    assert fake.list_thread_attachments.call_args.kwargs["agent"] == "dev_agent"
    assert fake.list_thread_attachments.call_args.kwargs["invocation_token"] == "tok-xyz"
    assert fake.list_thread_attachments.call_args.kwargs["thread_id"] == "THR-001"


def test_threads_attachments_get_passes_agent_and_token(
    monkeypatch, tmp_path: Path,
) -> None:
    """attachments get passes agent + invocation_token to the client."""
    from cli.commands.threads import cmd_threads_attachments_get
    fake = Mock()
    fake.get_thread_attachment.return_value = b"content"
    _stub_client(monkeypatch, fake)

    out = tmp_path / "out.bin"
    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", attachment_id="att-1",
        from_file=None, agent="dev_agent", invocation_token="tok-abc",
        output=str(out),
    )
    cmd_threads_attachments_get(args)

    assert fake.get_thread_attachment.call_args.kwargs["agent"] == "dev_agent"
    assert fake.get_thread_attachment.call_args.kwargs["invocation_token"] == "tok-abc"
    assert fake.get_thread_attachment.call_args.kwargs["thread_id"] == "THR-001"
    assert fake.get_thread_attachment.call_args.kwargs["attachment_id"] == "att-1"


def test_threads_attachments_get_from_file(
    monkeypatch, capsys, tmp_path: Path,
) -> None:
    """attachments get --from-file loads agent + token from JSON."""
    from cli.commands.threads import cmd_threads_attachments_get
    payload_path = tmp_path / "proof.json"
    payload_path.write_text(json.dumps({
        "thread_id": "THR-001",
        "attachment_id": "att-1",
        "agent": "dev_agent",
        "invocation_token": "tok-xyz",
    }))
    fake = Mock()
    fake.get_thread_attachment.return_value = b"content"
    _stub_client(monkeypatch, fake)

    out = tmp_path / "out.bin"
    args = argparse.Namespace(
        org="alpha", thread_id=None, attachment_id=None,
        from_file=str(payload_path), agent=None, invocation_token=None,
        output=str(out),
    )
    cmd_threads_attachments_get(args)

    assert fake.get_thread_attachment.call_args.kwargs["agent"] == "dev_agent"
    assert fake.get_thread_attachment.call_args.kwargs["invocation_token"] == "tok-xyz"
    assert out.read_bytes() == b"content"


def test_threads_attachments_list_missing_agent_exits_nonzero(
    monkeypatch, capsys,
) -> None:
    """attachments list without --agent exits nonzero and does not call client."""
    from cli.commands.threads import cmd_threads_attachments_list
    fake = Mock()
    fake.list_thread_attachments.return_value = {"attachments": []}
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", from_file=None,
        agent=None, invocation_token=None,
    )
    with pytest.raises(SystemExit) as exc_info:
        cmd_threads_attachments_list(args)
    assert exc_info.value.code != 0
    fake.list_thread_attachments.assert_not_called()


def test_threads_attachments_list_missing_token_exits_nonzero(
    monkeypatch, capsys,
) -> None:
    """attachments list with agent but no invocation_token exits nonzero."""
    from cli.commands.threads import cmd_threads_attachments_list
    fake = Mock()
    fake.list_thread_attachments.return_value = {"attachments": []}
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", from_file=None,
        agent="dev_agent", invocation_token=None,
    )
    with pytest.raises(SystemExit) as exc_info:
        cmd_threads_attachments_list(args)
    assert exc_info.value.code != 0
    fake.list_thread_attachments.assert_not_called()


def test_threads_attachments_get_missing_agent_exits_nonzero(
    monkeypatch, capsys, tmp_path: Path,
) -> None:
    """attachments get without --agent exits nonzero and does not call client."""
    from cli.commands.threads import cmd_threads_attachments_get
    fake = Mock()
    fake.get_thread_attachment.return_value = b"x"
    _stub_client(monkeypatch, fake)

    out = tmp_path / "out.bin"
    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", attachment_id="att-1",
        from_file=None, agent=None, invocation_token=None,
        output=str(out),
    )
    with pytest.raises(SystemExit) as exc_info:
        cmd_threads_attachments_get(args)
    assert exc_info.value.code != 0
    fake.get_thread_attachment.assert_not_called()


def test_threads_attachments_get_missing_token_exits_nonzero(
    monkeypatch, capsys, tmp_path: Path,
) -> None:
    """attachments get with agent but no invocation_token exits nonzero."""
    from cli.commands.threads import cmd_threads_attachments_get
    fake = Mock()
    fake.get_thread_attachment.return_value = b"x"
    _stub_client(monkeypatch, fake)

    out = tmp_path / "out.bin"
    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", attachment_id="att-1",
        from_file=None, agent="dev_agent", invocation_token=None,
        output=str(out),
    )
    with pytest.raises(SystemExit) as exc_info:
        cmd_threads_attachments_get(args)
    assert exc_info.value.code != 0
    fake.get_thread_attachment.assert_not_called()


def test_threads_attachments_list_founder_works(
    monkeypatch, capsys,
) -> None:
    """attachments list with agent=founder works (founder bearer path)."""
    from cli.commands.threads import cmd_threads_attachments_list
    fake = Mock()
    fake.list_thread_attachments.return_value = {"attachments": []}
    _stub_client(monkeypatch, fake)

    args = argparse.Namespace(
        org="alpha", thread_id="THR-001", from_file=None,
        agent="founder", invocation_token=None,
    )
    cmd_threads_attachments_list(args)

    # Founder path: agent passed, no token required.
    assert fake.list_thread_attachments.call_args.kwargs["agent"] == "founder"
    assert fake.list_thread_attachments.call_args.kwargs["invocation_token"] is None


# ── require_absolute_payload_path guard for thread commands ──────────

def test_threads_reply_rejects_relative_from_file(monkeypatch, capsys):
    """cmd_threads_reply exits 1 when --from-file is a relative path."""
    from cli.commands.threads import cmd_threads_reply
    from unittest.mock import Mock
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: Mock())
    monkeypatch.setattr(
        "cli.commands.threads._shared._fetch_available_orgs",
        lambda _client: ["alpha"],
    )
    args = argparse.Namespace(
        org="alpha", thread_id="THR-001",
        from_file="thread-reply.json", attach=None, shared=False,
    )
    with pytest.raises(SystemExit) as excinfo:
        cmd_threads_reply(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "absolute" in captured.err
    assert "thread-reply" in captured.err


def test_threads_decline_rejects_relative_from_file(monkeypatch, capsys):
    """cmd_threads_decline exits 1 when --from-file is a relative path."""
    from cli.commands.threads import cmd_threads_decline
    from unittest.mock import Mock
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: Mock())
    monkeypatch.setattr(
        "cli.commands.threads._shared._fetch_available_orgs",
        lambda _client: ["alpha"],
    )
    args = argparse.Namespace(org="alpha", thread_id="THR-001", from_file="decline.json")
    with pytest.raises(SystemExit) as excinfo:
        cmd_threads_decline(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "absolute" in captured.err
    assert "thread-decline" in captured.err


def test_threads_dispatch_rejects_relative_from_file(monkeypatch, capsys):
    """cmd_threads_dispatch exits 1 when --from-file is a relative path."""
    from cli.commands.threads import cmd_threads_dispatch
    from unittest.mock import Mock
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: Mock())
    monkeypatch.setattr(
        "cli.commands.threads._shared._fetch_available_orgs",
        lambda _client: ["alpha"],
    )
    args = argparse.Namespace(org="alpha", thread_id="THR-001", from_file="dispatch.json")
    with pytest.raises(SystemExit) as excinfo:
        cmd_threads_dispatch(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "absolute" in captured.err
    assert "thread-dispatch" in captured.err


def test_threads_compose_agent_rejects_relative_from_file(monkeypatch, capsys):
    """Agent-initiated cmd_threads_compose exits 1 when --from-file is relative."""
    from cli.commands.threads import cmd_threads_compose
    from unittest.mock import Mock
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: Mock())
    monkeypatch.setattr(
        "cli.commands.threads._shared._fetch_available_orgs",
        lambda _client: ["alpha"],
    )
    args = argparse.Namespace(
        org="alpha", task_id="TASK-001",
        from_file="compose.json", session_id=None,
        attach=[], shared=False,
    )
    with pytest.raises(SystemExit) as excinfo:
        cmd_threads_compose(args)
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "absolute" in captured.err
    assert "thread-compose" in captured.err
