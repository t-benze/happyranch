from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.daemon.org_state import OrgState

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


# C8 observes the installed candidate console and the shipping HTTP/DB seam.
# These private helpers never implement filtering or pagination.
def _seed_legacy_thread_list(org: OrgState) -> dict[str, list[dict]]:
    from datetime import timedelta
    from runtime.models import ThreadRecord, ThreadStatus

    expected: dict[str, list[dict]] = {"all": [], "open": [], "archived": []}
    # Input order is newest first; insert in a deliberately different order.
    # Unique timestamps independently establish the legacy descending oracle.
    for number in range(551, 0, -1):
        status = "archived" if number % 3 == 0 else "open"
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=number)
        subject = f"{org.slug} subject {number:04d} " + "x" * 70
        tid = f"THR-{number:04d}"
        participants = [] if number % 5 == 0 else [f"{org.slug}-current"]
        wire = {
            "thread_id": tid, "subject": subject, "status": status,
            "started_at": stamp.isoformat(),
            "archived_at": stamp.isoformat() if status == "archived" else None,
            "forwarded_from_id": None, "forwarded_from_kind": None,
            "turn_cap": 500, "turns_used": number % 7, "summary": None,
            "transcript_path": None, "composed_by": "founder",
            "composed_from_task_id": None, "composed_from_dream_id": None,
            "last_speaker": None, "pinned": False, "pinned_at": None,
            "last_activity_at": None, "participants": participants,
        }
        expected["all"].append(wire)
        expected[status].append(wire)
    # Interleave even/odd IDs so insertion order cannot supply the oracle.
    for wire in expected["all"][::2] + expected["all"][1::2]:
        org.db.insert_thread(ThreadRecord(
            id=wire["thread_id"], subject=wire["subject"],
            status=ThreadStatus(wire["status"]),
            started_at=datetime.fromisoformat(wire["started_at"]),
            archived_at=datetime.fromisoformat(wire["archived_at"]) if wire["archived_at"] else None,
            turn_cap=500, turns_used=wire["turns_used"],
        ))
        for name in wire["participants"]:
            assert org.db.add_thread_participant(wire["thread_id"], name, added_by="founder")
        assert org.db.add_thread_participant(wire["thread_id"], f"{org.slug}-removed", added_by="founder")
        assert org.db.remove_thread_participant(wire["thread_id"], f"{org.slug}-removed")
    return expected


# Context manager kept in this test owner rather than shared conftest.
@contextmanager
def _candidate_thread_list_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    import os
    import socket
    import threading
    import time
    import sys
    import uvicorn
    from starlette.types import Message, Receive, Scope, Send
    from runtime.config import Settings
    from runtime.daemon.app import create_app
    from runtime.daemon.org_state import OrgState
    from runtime.daemon.paths import ensure_token
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir

    home = tmp_path / "home"
    daemon_home = home / ".happyranch"
    state = None
    sock = None
    server = None
    worker = None
    worker_started = False
    stage = "private-home"
    ledger: list[dict] = []
    expected: dict[str, dict[str, list[dict]]] = {}
    errors: list[str] = []
    cleanup_errors: list[dict] = []
    acquired_orgs: dict[str, OrgState] = {}
    try:
        daemon_home.mkdir(parents=True)
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
        stage = "runtime-init"
        runtime = RuntimeDir.init(tmp_path / "runtime")
        (daemon_home / "runtimes.yaml").write_text("runtimes: []\n", encoding="utf-8")
        (daemon_home / "executors.json").write_text("{}", encoding="utf-8")
        stage = "private-token"
        ensure_token()  # fixture credential is never a receipt field
        stage = "state-init"
        # Retain the allocated state if __post_init__ fails after one store
        # has been attached. This invokes the unchanged shipping constructor.
        state = DaemonState.__new__(DaemonState)
        DaemonState.__init__(state, runtime=runtime, settings=Settings(project_root=runtime.root))
        stage = "socket-create"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        for slug in ("alpha", "beta"):
            root = runtime.orgs_dir / slug
            (root / "org").mkdir(parents=True)
            (root / "org" / "teams.yaml").write_text("teams: {}\n", encoding="utf-8")
            stage = f"org-load:{slug}"
            org = OrgState.load(slug=slug, root=root, settings=state.settings)
            acquired_orgs[slug] = org  # register before seeding or publication
            state.orgs[slug] = org
            stage = f"org-seed:{slug}"
            expected[slug] = _seed_legacy_thread_list(org)
        stage = "create-app"
        app = create_app(state)

        async def observed_app(scope: Scope, receive: Receive, send: Send) -> None:
            # Transparent ASGI observation: forward every byte unchanged, then
            # record only method/path/query/status/body, never request headers.
            record = {"method": scope["method"], "path": scope["path"],
                      "query": scope["query_string"].decode("ascii")}
            chunks: list[bytes] = []
            body_bytes = 0

            async def observed_send(message: Message) -> None:
                nonlocal body_bytes
                if message["type"] == "http.response.start":
                    record["status"] = message["status"]
                elif message["type"] == "http.response.body":
                    chunk = message.get("body", b"")
                    body_bytes += len(chunk)
                    if body_bytes > 1048576:
                        raise RuntimeError("C8 HTTP observation cap")
                    chunks.append(chunk)
                await send(message)

            try:
                await app(scope, receive, observed_send)
            finally:
                primary = sys.exc_info()[1]
                try:
                    record["body"] = json.loads(b"".join(chunks)) if chunks else None
                except BaseException as exc:
                    record["body_error"] = type(exc).__name__
                    if primary is None:
                        raise
                    primary.add_note("C8 HTTP observation failed: " + type(exc).__name__)
                finally:
                    ledger.append(record)

        stage = "socket-bind"
        sock.bind(("127.0.0.1", 0))
        address = sock.getsockname()
        (daemon_home / "daemon.port").write_text(str(address[1]), encoding="ascii")
        server = uvicorn.Server(uvicorn.Config(
            observed_app, host="127.0.0.1", port=address[1], lifespan="off",
            access_log=False, log_level="error", timeout_graceful_shutdown=1,
            loop="asyncio", http="h11", ws="none",
        ))

        def serve() -> None:
            try:
                server.run(sockets=[sock])
            except BaseException as exc:
                errors.append(type(exc).__name__)

        worker = threading.Thread(target=serve, name="candidate-thread-list", daemon=True)
        stage = "thread-start"
        worker.start()
        worker_started = True
        stage = "readiness"
        deadline = time.monotonic() + 5
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and worker.is_alive(), {"startup_errors": errors}
        child_env = {
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
            "HOME": str(home), "HAPPYRANCH_DAEMON_HOME": str(daemon_home),
            "HAPPYRANCH_ORG_SLUG": "", "TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        }
        for name, leaf in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                           ("XDG_DATA_HOME", "data"), ("TMPDIR", "tmp"), ("UV_CACHE_DIR", "uv-cache")):
            path = tmp_path / leaf
            path.mkdir()
            child_env[name] = str(path)
        stage = "body"
        yield {"expected": expected, "ledger": ledger, "env": child_env,
               "address": address, "socket_fd": sock.fileno(), "server_thread": worker.name, "server_pid": os.getpid()}
    finally:
        import sqlite3
        primary = sys.exc_info()[1]
        closed_dbs: list[str] = []
        closed_stores: list[str] = []

        def attempt(owner: str, close) -> None:
            try:
                close()
            except BaseException as exc:
                cleanup_errors.append({"owner": owner, "error": type(exc).__name__})

        if server is not None:
            attempt("server-stop", lambda: setattr(server, "should_exit", True))
        # A start exception may occur after native thread creation. ident is
        # checked independently; never join a merely allocated thread.
        thread_live = False
        if worker is not None:
            worker_started = worker_started or worker.ident is not None
            if worker_started:
                attempt("thread-join", lambda: worker.join(timeout=3))
                thread_live = worker.is_alive()
                if thread_live:
                    attempt("server-force-exit", lambda: setattr(server, "force_exit", True))
        # Socket close also runs when stop/join fails, before the final join.
        if sock is not None:
            attempt("socket-close", sock.close)
        if worker is not None and worker_started and thread_live:
            attempt("thread-final-join", lambda: worker.join(timeout=2))
        for slug, org in acquired_orgs.items():
            attempt(f"org-db-close:{slug}", org.db.close)

            def check_closed(org=org, slug=slug) -> None:
                try:
                    org.db._conn.execute("SELECT 1")
                except sqlite3.ProgrammingError:
                    closed_dbs.append(slug)
                else:
                    raise AssertionError("owned org DB remains open")

            attempt(f"org-db-observe:{slug}", check_closed)
        for name in ("metrics_store", "direct_connect_authority_store"):
            store = getattr(state, name, None) if state is not None else None
            if store is not None:
                def close_store(store=store, name=name) -> None:
                    store.close()
                    closed_stores.append(name)
                attempt(name, close_store)
        teardown = {"server_stopped": worker is None or not worker_started or not worker.is_alive(),
                    "thread_allocated": worker is not None, "thread_started": worker_started,
                    "socket_closed": sock is None or sock.fileno() == -1,
                    "server_errors": errors, "closed_org_dbs": closed_dbs,
                    "acquired_org_dbs": list(acquired_orgs), "closed_stores": closed_stores,
                    "stage": stage, "primary_error": None if primary is None else type(primary).__name__,
                    "cleanup_errors": cleanup_errors,
                    # A constructor that never returned keeps its internal
                    # acquisition under its shipping owner; do not assert it
                    # closed merely because no handle reached this fixture.
                    "partial_acquisition": stage if primary is not None and stage in
                        ("runtime-init", "state-init", "socket-create", "org-load:alpha", "org-load:beta") else None}
        attempt("http-ledger-write", lambda: (tmp_path / "c8-http.json").write_text(
            json.dumps(ledger, indent=2) + "\n", encoding="utf-8"))
        attempt("teardown-write", lambda: (tmp_path / "c8-teardown.json").write_text(
            json.dumps(teardown, indent=2) + "\n", encoding="utf-8"))
        # All owners and diagnostics were attempted before closure assertions.
        if primary is not None:
            primary.add_note("C8 teardown: " + json.dumps(teardown))
        else:
            assert teardown["server_stopped"] and teardown["socket_closed"], teardown
            assert closed_dbs == list(acquired_orgs), teardown
            assert not errors and not cleanup_errors, teardown


def _candidate_console_child(argv: list[str], *, source: Path, env: dict,
                             deadline: float, receipt: dict, expected_kernel_argv=None) -> tuple[str, str]:
    """Own one actual child, finite pipes, and independent finalization attempts."""
    import os
    import selectors
    import subprocess
    import sys
    import time

    process = None
    selector = None
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    cleanup_errors: list[dict] = []
    receipt.update(stage="spawn", reaped=False, cleanup_errors=cleanup_errors)
    try:
        process = subprocess.Popen(argv, cwd=source, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        receipt.update(pid=process.pid, stage="kernel-identity")
        child_deadline = min(deadline, time.monotonic() + 5)
        if expected_kernel_argv is not None and sys.platform == "linux":
            proc = Path(f"/proc/{process.pid}")
            actual_executable = (proc / "exe").resolve(strict=True)
            raw_cmdline = (proc / "cmdline").read_bytes()
            # exec can briefly expose an empty cmdline. Observe the same child
            # within its existing budget; never accept missing identity data.
            while not raw_cmdline and process.poll() is None and time.monotonic() < child_deadline:
                time.sleep(0.001)
                raw_cmdline = (proc / "cmdline").read_bytes()
            assert raw_cmdline.endswith(b"\0"), receipt
            # Remove only the record terminator: --status "" has a real final
            # empty argument, which rstrip would silently discard.
            actual_argv = raw_cmdline[:-1].decode().split("\0")
            receipt.update(actual_executable=str(actual_executable), actual_argv=actual_argv)
            assert actual_executable == Path(sys.executable).resolve()
            assert actual_argv == expected_kernel_argv, receipt
        receipt["stage"] = "drain"
        selector = selectors.DefaultSelector()
        for name in outputs:
            pipe = getattr(process, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = child_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("C8 child output deadline")
            for key, _events in selector.select(min(0.05, remaining)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    target = outputs[key.data]
                    if len(target) + len(chunk) > 1048576:
                        target.extend(chunk[:1048576 - len(target)])
                        receipt["output_cap"] = key.data
                        raise RuntimeError("C8 child output cap")
                    target.extend(chunk)
        remaining = child_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("C8 child reap deadline")
        process.wait(timeout=remaining)
        receipt["stage"] = "finished"
        return (outputs["stdout"].decode("utf-8"), outputs["stderr"].decode("utf-8"))
    except BaseException as exc:
        receipt["primary_error"] = type(exc).__name__
        raise
    finally:
        primary = sys.exc_info()[1]

        def attempt(owner: str, close) -> None:
            try:
                close()
            except BaseException as exc:
                cleanup_errors.append({"owner": owner, "error": type(exc).__name__})

        if process is not None:
            if process.poll() is None:
                attempt("child-kill", process.kill)
            attempt("child-reap", lambda: process.wait(timeout=min(2, max(0.01, deadline - time.monotonic()))))
            # Each pipe close runs even when the other close/reap fails.
            for name in outputs:
                pipe = getattr(process, name, None)
                if pipe is not None:
                    attempt(name + "-close", pipe.close)
            receipt.update(exit_code=process.returncode, reaped=process.poll() is not None,
                           residual_pid=None if process.poll() is not None else process.pid)
        if selector is not None:
            attempt("selector-close", selector.close)
        receipt.update(stdout=outputs["stdout"].decode("utf-8", errors="replace"),
                       stderr=outputs["stderr"].decode("utf-8", errors="replace"))
        if primary is not None:
            primary.add_note("C8 child cleanup: " + json.dumps(cleanup_errors))
        else:
            assert receipt["reaped"] and not cleanup_errors, receipt


def test_threads_list_legacy_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import os
    import sys
    import time
    from urllib.parse import parse_qsl
    import cli.main
    import cli.client.client
    import runtime.daemon.routes.threads
    import runtime.infrastructure.database

    source = Path(__file__).resolve().parents[1]
    console = Path(sys.prefix) / "bin" / "happyranch"
    # Fail closed on a foreign installed console/import, even with correct cwd.
    for module in (cli.main, cli.client.client, runtime.daemon.routes.threads, runtime.infrastructure.database):
        assert Path(module.__file__).resolve().is_relative_to(source), module.__name__
    assert console.is_file() and os.access(console, os.X_OK)
    shebang = console.read_text(encoding="utf-8").splitlines()[0]
    assert shebang.startswith("#!"), "candidate console has no interpreter entry"
    console_interpreter = Path(shebang[2:])
    assert console_interpreter.is_absolute() and console_interpreter.samefile(sys.executable)
    console_digest = hashlib.sha256(console.read_bytes()).hexdigest()
    # Twelve inputs, each in both equal-ID orgs; one finite node, 24 real CLI
    # processes. Oracles are input-derived, not candidate DB list results.
    cases = [
        ("default", [], [("limit", "50")], "all", 50),
        ("open", ["--status", "open"], [("limit", "50"), ("status", "open")], "open", 50),
        ("archived", ["--status", "archived"], [("limit", "50"), ("status", "archived")], "archived", 50),
        ("zero", ["--limit", "0"], [("limit", "0")], "all", 0),
        ("one", ["--limit", "1"], [("limit", "1")], "all", 1),
        ("500", ["--limit", "500"], [("limit", "500")], "all", 500),
        ("999", ["--limit", "999"], [("limit", "999")], "all", 500),
        ("1000", ["--limit", "1000"], [("limit", "1000")], "all", 500),
        ("negative1", ["--limit", "-1"], [("limit", "-1")], "all", 551),
        ("negative2", ["--limit", "-2"], [("limit", "-2")], "all", 551),
        ("unknown", ["--status", "unknown"], [("limit", "50"), ("status", "unknown")], "all", 0),
        ("empty", ["--status", ""], [("limit", "50")], "all", 50),
    ]
    provenance_code = """
import hashlib, importlib, json, pathlib, sys
source = pathlib.Path(sys.argv[1]).resolve()
# -c normally supplies cwd as sys.path[0]; installed console supplies bin.
sys.path[0] = sys.argv[2]
modules = {}
for name in ('cli.main', 'cli.commands.threads', 'cli.client.client',
             'runtime.runtime', 'runtime.daemon.routes.threads', 'runtime.infrastructure.database'):
    module = importlib.import_module(name)
    path = pathlib.Path(module.__file__).resolve()
    assert path.is_relative_to(source), (name, str(path))
    modules[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
print(json.dumps({'python': sys.executable, 'version': sys.version, 'modules': modules}))
"""
    deadline = time.monotonic() + 90
    receipts: list[dict] = []
    try:
        with _candidate_thread_list_server(tmp_path, monkeypatch) as fixture:
            provenance_argv = [sys.executable, "-c", provenance_code, str(source), str(console.parent)]
            provenance_receipt = {"case": "console-import-provenance", "argv": provenance_argv}
            receipts.append(provenance_receipt)
            provenance_stdout, provenance_stderr = _candidate_console_child(
                provenance_argv, source=source, env=fixture["env"], deadline=deadline,
                receipt=provenance_receipt)
            assert provenance_receipt["exit_code"] == 0 and provenance_stderr == "", provenance_receipt
            json.loads(provenance_stdout)
            for slug in ("alpha", "beta"):
                for name, flags, query, bucket, count in cases:
                    oracle = fixture["expected"][slug][bucket][:count]
                    before = len(fixture["ledger"])
                    argv = [str(console), "threads", "list", "--org", slug, *flags]
                    receipt = {"case": name, "org": slug, "argv": argv, "cwd": str(source),
                               "executable": str(console), "console_sha256": console_digest,
                               "console_interpreter": str(console_interpreter),
                               "loopback": fixture["address"], "socket_fd": fixture["socket_fd"],
                               "server_thread": fixture["server_thread"], "server_pid": fixture["server_pid"],
                               "child_env": fixture["env"]}
                    receipts.append(receipt)
                    stdout, stderr = _candidate_console_child(
                        argv, source=source, env=fixture["env"], deadline=deadline,
                        receipt=receipt, expected_kernel_argv=[str(console_interpreter), *argv])
                    assert receipt["exit_code"] == 0, receipt
                    assert stderr == "", receipt
                    # Observation finishes after the ASGI app returns; do not
                    # race its final ledger append against child process exit.
                    until = time.monotonic() + 1
                    while len(fixture["ledger"]) < before + 2 and time.monotonic() < until:
                        time.sleep(0.01)
                    observed = fixture["ledger"][before:]
                    assert [(r["method"], r["path"]) for r in observed] == [
                        ("GET", "/api/v1/orgs"), ("GET", f"/api/v1/orgs/{slug}/threads"),
                    ]
                    assert observed[0]["query"] == ""
                    assert [r["status"] for r in observed] == [200, 200]
                    assert [org["slug"] for org in observed[0]["body"]["orgs"]] == ["alpha", "beta"]
                    assert observed[1]["query"] == "&".join(f"{key}={value}" for key, value in query)
                    assert parse_qsl(observed[1]["query"], keep_blank_values=True) == query
                    assert set(observed[1]["body"]) == {"threads"}  # legacy envelope, no page metadata
                    assert observed[1]["body"]["threads"] == oracle  # order, fields, current/empty participants, org exclusion
                    lines = [
                        f"{row['thread_id']:10s}  {row['status']:12s}  "
                        f"turns={row['turns_used']}/{row['turn_cap']}  "
                        f"{datetime.fromisoformat(row['started_at']).strftime('%Y-%m-%d %H:%M:%S')}  {row['subject'][:60]}\n"
                        for row in oracle
                    ]
                    assert stdout == "".join(lines), receipt
    finally:
        primary = sys.exc_info()[1]
        try:
            (tmp_path / "c8-console.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
        except BaseException as exc:
            if primary is None:
                raise
            primary.add_note("C8 console receipt write failed: " + type(exc).__name__)
