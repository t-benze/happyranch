"""THR-055 B1-R1 — CLI tests for `happyranch skills create`.

Covers:
- argparse required flags (--from-file, --session-id), optional --org
- Missing-file and invalid-JSON error handling
- Body sanitization (identity-key rejection)
- Bearer-free transport (no Authorization header)
- Success output format
- Daemon lifecycle validation error rendering (exact format + exit code)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Ensure cli/commands is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

_VALID_PROPOSAL = {
    "slug": "frontend-testing",
    "name": "Frontend Testing",
    "description": "A skill for frontend testing.",
    "version": "0.1.0",
    "policy_class": "standard_operational",
    "skill_md": "# Frontend Testing\n\nGuidelines.",
    "purpose": "Help dev agents write better tests",
    "target_agent_suggestion": "dev_agent",
    "references": None,
    "assets": None,
}


def _write_proposal_json(content: dict, suffix: str = ".json") -> str:
    """Write a proposal dict to a temp file, return the absolute path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(content, f)
    return path


def _mock_response(status: int, json_body: dict | None = None, text: str = ""):
    """Build a mock httpx.Response with real int status_code."""
    mock = MagicMock()
    mock.status_code = status  # plain int, NOT PropertyMock
    if json_body is not None:
        mock.json.return_value = json_body
    else:
        mock.json.side_effect = ValueError("not json")
    mock.text = text
    return mock


# patch targets: port_file is imported locally via
#   from cli.client.client import port_file
# and httpx.Client is accessed via a local `import httpx` inside
# cmd_skills_create, so patch at their source modules.
_PORT_FILE_PATCH = "cli.client.client.port_file"
_HTTPX_CLIENT_PATCH = "httpx.Client"


def _setup_transport_mocks(
    port_mock,
    httpx_client_mock,
    *,
    post_response: MagicMock | None = None,
    orgs_response: MagicMock | None = None,
):
    """Configure port_file and httpx.Client mocks for cmd_skills_create."""
    port_fake = MagicMock()
    port_fake.exists.return_value = True
    port_fake.read_text.return_value = "19999"
    port_mock.return_value = port_fake

    client_instance = MagicMock()
    if orgs_response is not None:
        client_instance.get.return_value = orgs_response
    else:
        orgs_resp = _mock_response(200, {"orgs": [{"slug": "alpha"}]})
        client_instance.get.return_value = orgs_resp

    if post_response is not None:
        client_instance.post.return_value = post_response
    else:
        success_resp = _mock_response(201, {
            "status": "proposed",
            "skill_id": "hr:frontend-testing",
            "version_id": 42,
            "version": "0.1.0",
            "content_hash": "abc123def456", "policy_class": "standard_operational",
        })
        client_instance.post.return_value = success_resp

    httpx_client_mock.return_value = client_instance
    return port_fake, client_instance


def _run_create(args_dict: dict, capsys, expect_exit: bool = True) -> int | None:
    """Run cmd_skills_create with given args dict, return exit code or None."""
    from cli.commands.skills import cmd_skills_create
    import argparse

    ns = argparse.Namespace(**args_dict)
    if expect_exit:
        with pytest.raises(SystemExit) as exc:
            cmd_skills_create(ns)
        return exc.value.code
    else:
        cmd_skills_create(ns)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CLI argument parsing tests
# ═══════════════════════════════════════════════════════════════════════════

class TestArgparseRequiredFlags:
    """Tests for required CLI flags (--from-file, --session-id)."""

    def test_missing_from_file_exits(self, capsys):
        """--from-file is required."""
        code = _run_create({
            "from_file": None,
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "--from-file" in err

    def test_missing_session_id_exits(self, capsys):
        """--session-id is required."""
        p = _write_proposal_json(_VALID_PROPOSAL)
        code = _run_create({
            "from_file": p,
            "session_id": None,
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "--session-id" in err

    def test_org_is_optional(self, capsys):
        """--org is optional; resolves from daemon if absent."""
        p = _write_proposal_json(_VALID_PROPOSAL)
        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _setup_transport_mocks(port_mock, client_mock)
            code = _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": None,
            }, capsys, expect_exit=False)
        out = capsys.readouterr().out
        assert "Custom skill created successfully" in out


class TestFromFile:
    """Tests for --from-file validation."""

    def test_missing_file_exits(self, capsys):
        """Non-existent --from-file exits with error."""
        code = _run_create({
            "from_file": "/nonexistent/path/proposal.json",
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "Error reading payload file" in err

    def test_invalid_json_exits(self, capsys):
        """Malformed JSON in --from-file exits with clear error."""
        fd, p = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("not json {{{")
        code = _run_create({
            "from_file": p,
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "Error reading payload file" in err


# ═══════════════════════════════════════════════════════════════════════════
# Body sanitization tests
# ═══════════════════════════════════════════════════════════════════════════

class TestBodySanitization:
    """Tests that identity keys in the JSON body are rejected."""

    def test_task_id_in_body_rejected(self, capsys):
        body = dict(_VALID_PROPOSAL, task_id="TASK-spoof")
        p = _write_proposal_json(body)
        code = _run_create({
            "from_file": p,
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "task_id" in err
        assert "must not contain" in err

    def test_session_id_in_body_rejected(self, capsys):
        body = dict(_VALID_PROPOSAL, session_id="sess-spoof")
        p = _write_proposal_json(body)
        code = _run_create({
            "from_file": p,
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "session_id" in err
        assert "must not contain" in err

    def test_proposer_agent_in_body_rejected(self, capsys):
        body = dict(_VALID_PROPOSAL, proposer_agent="engineering_manager")
        p = _write_proposal_json(body)
        code = _run_create({
            "from_file": p,
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "proposer_agent" in err
        assert "must not contain" in err

    def test_agent_name_in_body_rejected(self, capsys):
        """agent_name (the identity parameter) is rejected from body."""
        body = dict(_VALID_PROPOSAL, agent_name="engineering_manager")
        p = _write_proposal_json(body)
        code = _run_create({
            "from_file": p,
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "agent_name" in err
        assert "must not contain" in err

    def test_org_in_body_rejected(self, capsys):
        body = dict(_VALID_PROPOSAL, org="bad")
        p = _write_proposal_json(body)
        code = _run_create({
            "from_file": p,
            "session_id": "sess-1",
            "org": "alpha",
        }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "org" in err
        assert "must not contain" in err

    def test_pure_package_body_passes_sanitization(self, capsys):
        """A body containing only known package fields passes body validation."""
        body = dict(_VALID_PROPOSAL)  # all fields are package-content only
        p = _write_proposal_json(body)
        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _setup_transport_mocks(port_mock, client_mock)
            code = _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": "alpha",
            }, capsys, expect_exit=False)
        out = capsys.readouterr().out
        assert "Custom skill created successfully" in out


# ═══════════════════════════════════════════════════════════════════════════
# Transport tests (no bearer token, correct POST shape, error rendering)
# ═══════════════════════════════════════════════════════════════════════════

class TestBearerFreeTransport:
    """Tests for the bearer-free session-proposal transport."""

    def test_no_authorization_header_sent(self, capsys):
        """The POST client must NOT send an Authorization header."""
        p = _write_proposal_json(_VALID_PROPOSAL)
        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _, client_instance = _setup_transport_mocks(port_mock, client_mock)
            _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": "alpha",
            }, capsys, expect_exit=False)

        # Verify httpx.Client was constructed without Authorization
        call_kwargs = client_mock.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert "authorization" not in {k.lower() for k in headers}

    def test_posts_correct_path_and_params(self, capsys):
        """submit_proposal POSTs to the correct path with query params."""
        p = _write_proposal_json(_VALID_PROPOSAL)
        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _, client_instance = _setup_transport_mocks(port_mock, client_mock)
            _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": "alpha",
            }, capsys, expect_exit=False)

        client_instance.post.assert_called_once()
        call_args, call_kwargs = client_instance.post.call_args
        assert "skills/agent" in call_args[0]
        assert call_kwargs["params"] == {"session_id": "sess-1"}
        assert call_kwargs["json"] == _VALID_PROPOSAL

    def test_success_output_format(self, capsys):
        """Success response prints structured output."""
        p = _write_proposal_json(_VALID_PROPOSAL)
        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _setup_transport_mocks(port_mock, client_mock)
            _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": "alpha",
            }, capsys, expect_exit=False)

        out = capsys.readouterr().out
        assert "Custom skill created successfully" in out
        assert "skill_id" in out
        assert "hr:frontend-testing" in out
        assert "42" in out
        assert "abc123def456" in out

    def test_lifecycle_error_rendered_exact_form(self, capsys):
        """Lifecycle 4xx/422 errors are rendered as 'error (<status>): <detail>'.

        This is a regression test per TASK-3591 review finding: the protocol
        must reflect the exact shipping format. The CLI prints
        ``error (<HTTP-status>): <detail>`` (not ``error: [<code>] <detail>``)
        and exits with code 1.
        """
        p = _write_proposal_json(_VALID_PROPOSAL)
        error_body = {
            "detail": {
                "code": "protected_slug",
                "detail": "Slug 'start-task' is a protected system skill.",
            }
        }
        post_resp = _mock_response(422, error_body)

        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _setup_transport_mocks(port_mock, client_mock, post_response=post_resp)
            code = _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": "alpha",
            }, capsys)

        err = capsys.readouterr().err
        # Exact form: "error (<HTTP-status>): <detail>" and exit 1
        assert code == 1
        assert err.startswith("error (422): ")
        assert "protected_slug" in err
        assert "protected system skill" in err

    def test_internal_server_error_rendered(self, capsys):
        """Daemon 500 errors (JSON detail) print status code + detail."""
        p = _write_proposal_json(_VALID_PROPOSAL)
        error_body = {"detail": "Internal Server Error"}
        post_resp = _mock_response(500, error_body)

        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _setup_transport_mocks(port_mock, client_mock, post_response=post_resp)
            code = _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": "alpha",
            }, capsys)

        err = capsys.readouterr().err
        assert code == 1
        assert "error (500): " in err
        assert "Internal Server Error" in err

    def test_non_json_response_raises_before_renderer(self, capsys):
        """A non-JSON daemon response (e.g. HTML 502) raises ValueError.

        The CLI unconditionally calls resp.json() (cli/commands/skills.py:685);
        a non-JSON response raises before the error renderer or exit handling.
        There is no fallback to response text.
        """
        p = _write_proposal_json(_VALID_PROPOSAL)
        # _mock_response with json_body=None -> json() raises ValueError
        post_resp = _mock_response(502, json_body=None, text="<html>502 Bad Gateway</html>")

        with patch(_PORT_FILE_PATCH) as port_mock, \
             patch(_HTTPX_CLIENT_PATCH) as client_mock:
            _setup_transport_mocks(port_mock, client_mock, post_response=post_resp)
            with pytest.raises(ValueError, match="not json"):
                _run_create({
                    "from_file": p,
                    "session_id": "sess-1",
                    "org": "alpha",
                }, capsys, expect_exit=False)

    def test_daemon_not_running_exits(self, capsys):
        """When daemon port file doesn't exist, exit with clear message."""
        p = _write_proposal_json(_VALID_PROPOSAL)
        with patch(_PORT_FILE_PATCH) as port_mock:
            port_fake = MagicMock()
            port_fake.exists.return_value = False
            port_mock.return_value = port_fake
            code = _run_create({
                "from_file": p,
                "session_id": "sess-1",
                "org": "alpha",
            }, capsys)
        assert code == 1
        err = capsys.readouterr().err
        assert "daemon not running" in err

    def test_create_subcommand_registered(self):
        """`skills create` is registered in the argument parser."""
        from cli.main import build_parser

        parser = build_parser()
        # verify the parser accepts the propose subcommand
        try:
            args = parser.parse_args(["skills", "propose",
                                      "--from-file", "/tmp/x.json",
                                      "--session-id", "s-1",
                                      "--org", "x"])
        except SystemExit:
            pass


class TestProposeSubcommandDiscovery:
    """Smoke test for subcommand presence in parser."""

    def test_create_appears_in_help(self, capsys):
        """`propose` appears in the skills subcommand help output."""
        from cli.main import build_parser

        parser = build_parser()
        try:
            parser.parse_args(["skills", "--help"])
        except SystemExit:
            pass
        out, _ = capsys.readouterr()
        assert "create" in out
