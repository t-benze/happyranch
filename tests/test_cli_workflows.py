from __future__ import annotations

import argparse
import json
from unittest.mock import Mock, patch

import pytest

from cli.commands.workflows import (
    cmd_workflow_templates_list,
    cmd_workflow_templates_publish,
    cmd_workflow_templates_show,
)
from cli.main import build_parser
from tests.workflows.test_template_store import VALID_DEFINITION


def _response(status: int, payload: dict) -> Mock:
    response = Mock()
    response.status_code = status
    response.json.return_value = payload
    response.text = json.dumps(payload)
    return response


def _payload(tmp_path) -> str:
    path = tmp_path / "template.json"
    path.write_text(json.dumps({
        "operation_key": "op-1",
        "template_name": "product-design",
        "expected_current_version": 0,
        "definition": VALID_DEFINITION,
    }))
    return str(path)


def test_workflows_templates_parser_contract() -> None:
    args = build_parser().parse_args([
        "workflows", "templates", "publish", "--from-file", "/tmp/template.json",
        "--session-id", "sess-1", "--org", "alpha",
    ])
    assert args.from_file == "/tmp/template.json"
    assert args.session_id == "sess-1"
    assert args.org == "alpha"
    assert build_parser().parse_args([
        "workflows", "templates", "list", "--team", "engineering", "--org", "alpha",
    ]).team == "engineering"
    assert build_parser().parse_args([
        "workflows", "templates", "show", "engineering", "product-design", "1",
        "--org", "alpha",
    ]).version == 1


def test_publish_rejects_relative_from_file_before_transport(capsys) -> None:
    args = argparse.Namespace(
        from_file="template.json", session_id="sess-1", org="alpha", json=False,
    )
    with pytest.raises(SystemExit) as exc:
        cmd_workflow_templates_publish(args)
    assert exc.value.code == 1
    assert "must be absolute" in capsys.readouterr().err


def test_agent_publish_is_token_free_and_forwards_only_session_id(tmp_path, capsys) -> None:
    result = {
        "namespace": "org/alpha/team/engineering", "template_name": "product-design",
        "version": 1, "definition_digest": "d", "publisher": {"principal_id": "manager"},
    }
    transport = Mock()
    transport.post.return_value = _response(201, result)
    args = argparse.Namespace(
        from_file=_payload(tmp_path), session_id="sess-1", org="alpha", json=True,
    )
    daemon_port = tmp_path / "daemon.port"
    daemon_port.write_text("9345")
    with patch("cli.commands.workflows.httpx.Client", return_value=transport) as factory, patch(
        "cli.commands.workflows.port_file", return_value=daemon_port,
    ):
        cmd_workflow_templates_publish(args)
    assert json.loads(capsys.readouterr().out) == result
    assert "Authorization" not in factory.call_args.kwargs["headers"]
    transport.post.assert_called_once_with(
        "/api/v1/orgs/alpha/workflows/templates/publish",
        json=json.loads(open(args.from_file).read()),
        params={"session_id": "sess-1"},
    )


def test_founder_publish_uses_existing_bearer_client(tmp_path, capsys) -> None:
    payload = json.loads(open(_payload(tmp_path)).read())
    payload["team_slug"] = "engineering"
    path = tmp_path / "founder.json"
    path.write_text(json.dumps(payload))
    client = Mock()
    client.post.return_value = _response(201, {
        "namespace": "org/alpha/team/engineering", "template_name": "product-design",
        "version": 1, "definition_digest": "digest", "publisher": {"principal_id": "founder"},
    })
    args = argparse.Namespace(from_file=str(path), session_id=None, org="alpha", json=False)
    with patch("cli.commands.workflows.OpcClient.from_env", return_value=client), patch(
        "cli.commands.workflows._shared._fetch_available_orgs", return_value=["alpha"],
    ):
        cmd_workflow_templates_publish(args)
    client.post.assert_called_once_with(
        "/api/v1/orgs/alpha/workflows/templates/publish", json=payload,
    )
    assert "published org/alpha/team/engineering/product-design v1" in capsys.readouterr().out


def test_list_and_show_use_founder_client_and_render_exact_payload(capsys) -> None:
    item = {
        "namespace": "org/alpha/team/engineering", "template_name": "product-design",
        "version": 1, "definition_digest": "digest", "publisher": {"principal_id": "founder"},
    }
    client = Mock()
    client.get.side_effect = [_response(200, {"templates": [item]}), _response(200, item)]
    with patch("cli.commands.workflows.OpcClient.from_env", return_value=client), patch(
        "cli.commands.workflows._shared._fetch_available_orgs", return_value=["alpha"],
    ):
        cmd_workflow_templates_list(argparse.Namespace(org="alpha", team="engineering", json=True))
        cmd_workflow_templates_show(argparse.Namespace(
            org="alpha", team="engineering", template_name="product-design",
            version=1, json=True,
        ))
    output = capsys.readouterr().out.strip().splitlines()
    assert json.loads(output[0]) == {"templates": [item]}
    assert json.loads(output[1]) == item


@pytest.mark.parametrize(
    "code",
    ["template_publish_operation_conflict", "template_version_cas_stale",
     "template_content_already_published", "manager_required"],
)
def test_cli_surfaces_route_machine_code_unchanged(tmp_path, capsys, code: str) -> None:
    transport = Mock()
    transport.post.return_value = _response(409, {"detail": {"code": code}})
    args = argparse.Namespace(
        from_file=_payload(tmp_path), session_id="sess-1", org="alpha", json=False,
    )
    daemon_port = tmp_path / "daemon.port"
    daemon_port.write_text("9345")
    with patch("cli.commands.workflows.httpx.Client", return_value=transport), patch(
        "cli.commands.workflows.port_file", return_value=daemon_port,
    ):
        with pytest.raises(SystemExit) as exc:
            cmd_workflow_templates_publish(args)
    assert exc.value.code == 1
    assert code in capsys.readouterr().err
