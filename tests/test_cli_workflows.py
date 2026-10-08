from __future__ import annotations

import argparse
import json
from pathlib import Path
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


@pytest.mark.parametrize("form", ["show", "request", "downgrade-preflight"])
def test_cutover_parser_and_usage_contract(form: str) -> None:
    words = ["workflows", "cutover", form, "--org", "alpha", "--json"]
    if form == "request":
        words += ["--from-file", "/tmp/cutover.json"]
    args = build_parser().parse_args(words)
    assert args.org == "alpha" and args.json
    assert args.cutover_command == form
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(words + ["--verified"])
    assert exc.value.code == 2


@pytest.mark.parametrize("form,eligible,expected_exit", [
    ("show", None, None), ("request", None, None),
    ("downgrade-preflight", True, None), ("downgrade-preflight", False, 1),
])
def test_cutover_cli_wire_projection_and_pending_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture, form: str, eligible: bool | None, expected_exit: int | None,
) -> None:
    from cli.commands.workflows import cmd_workflow_cutover

    projection = {"org_slug": "alpha", "state": "enable_requested", "generation": 2, "blockers": []}
    result = projection if eligible is None else {"eligible": eligible, "blockers": [], "projection": projection}
    body = {"action": "enable", "operation_key": "one", "expected_generation": 1}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(body))
    args = argparse.Namespace(cutover_command=form, org="alpha", json=True, from_file=str(path))
    client = Mock()
    client.post.return_value = client.get.return_value = _response(200, result)
    with patch("cli.commands.workflows.OpcClient.from_env", return_value=client), patch(
        "cli.commands.workflows._shared._fetch_available_orgs", return_value=["alpha"],
    ):
        if expected_exit is None:
            cmd_workflow_cutover(args)
        else:
            with pytest.raises(SystemExit) as exc:
                cmd_workflow_cutover(args)
            assert exc.value.code == expected_exit
    assert json.loads(capsys.readouterr().out) == result
    if form == "request":
        client.post.assert_called_once_with("/api/v1/orgs/alpha/workflows/cutover/requests", json=body)
    else:
        client.get.assert_called_once_with("/api/v1/orgs/alpha/workflows/cutover" + ("/downgrade-preflight" if eligible is not None else ""))


@pytest.mark.parametrize("payload", [b"{", b"[]", b"\xff"])
def test_cutover_cli_bad_file_never_opens_transport(tmp_path: Path, payload: bytes) -> None:
    from cli.commands.workflows import cmd_workflow_cutover

    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    with patch("cli.commands.workflows.OpcClient.from_env") as transport:
        with pytest.raises(SystemExit) as exc:
            cmd_workflow_cutover(argparse.Namespace(cutover_command="request", from_file=str(path), org="alpha", json=False))
        assert exc.value.code == 1
        transport.assert_not_called()


@pytest.mark.parametrize("failure", ["domain", "transport", "relative"])
def test_cutover_cli_errors_are_nonzero_and_keep_safe_categories(
    tmp_path: Path, capsys: pytest.CaptureFixture, failure: str,
) -> None:
    import httpx
    from cli.commands.workflows import cmd_workflow_cutover

    path = tmp_path / "request.json"
    path.write_text('{"action":"enable","operation_key":"one","expected_generation":1}')
    client = Mock()
    if failure == "domain":
        client.post.return_value = _response(409, {"detail": {"code": "cutover_generation_stale"}})
    elif failure == "transport":
        client.post.side_effect = httpx.ConnectError("private transport detail")
    with patch("cli.commands.workflows.OpcClient.from_env", return_value=client), patch(
        "cli.commands.workflows._shared._fetch_available_orgs", return_value=["alpha"],
    ):
        with pytest.raises(SystemExit) as exc:
            cmd_workflow_cutover(argparse.Namespace(cutover_command="request", from_file="relative.json" if failure == "relative" else str(path), org="alpha", json=False))
    assert exc.value.code == 1
    error = capsys.readouterr().err
    assert {"domain": "cutover_generation_stale", "transport": "retry requests with the same body/key", "relative": "must be absolute"}[failure] in error
    assert "private transport detail" not in error


def test_actual_cutover_cli_f_remedy_and_refusal_use_real_http_service(tmp_path: Path, capsys, monkeypatch) -> None:
    """YES test transport seam: OpcClient.from_env uses real TestClient HTTP."""
    from fastapi.testclient import TestClient
    from cli.commands.workflows import cmd_workflow_cutover
    from runtime.config import Settings
    from runtime.daemon.app import create_app
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir
    from tests.daemon.test_org_state import _seed_org
    home = tmp_path / 'daemon-home'
    home.mkdir()
    monkeypatch.setenv('HAPPYRANCH_DAEMON_HOME',str(home))
    from runtime.daemon.paths import ensure_token
    token = ensure_token()
    runtime = RuntimeDir.init(tmp_path / 'runtime')
    _seed_org(runtime.orgs_dir / 'alpha')
    state = DaemonState.from_runtime(runtime,Settings())
    client = TestClient(create_app(state),headers={'Authorization':f'Bearer {token}'})
    monkeypatch.setattr('cli.commands.workflows.OpcClient.from_env',lambda:client)
    org = state.orgs['alpha']
    try:
        args = argparse.Namespace(cutover_command='show',org='alpha',json=False)
        cmd_workflow_cutover(args)
        output = capsys.readouterr().out
        assert 'migrate_workflow_draft_schema.py' in output and '--org alpha' in output
        assert str(runtime.root) in output
        payload = tmp_path / 'request.json'
        payload.write_text(json.dumps(dict(action='enable',operation_key='new',expected_generation=1)))
        before = tuple(org.db._conn.iterdump())
        with pytest.raises(SystemExit) as exc:
            cmd_workflow_cutover(argparse.Namespace(cutover_command='request',org='alpha',json=False,from_file=str(payload)))
        assert exc.value.code == 1
        assert 'draft_schema_migration_required' in capsys.readouterr().err
        assert tuple(org.db._conn.iterdump()) == before
    finally:
        org.close()
        client.close()


@pytest.mark.parametrize('words', [
    ['activate', '--from-file', '/tmp/activation.json'],
    ['activations', 'list'], ['activations', 'show', 'activation:one'],
])
def test_activation_cli_parser_requires_org_and_rejects_session_claim(words):
    with pytest.raises(SystemExit) as missing:
        build_parser().parse_args(['workflows', *words])
    assert missing.value.code == 2
    args = build_parser().parse_args(['workflows', *words, '--org', 'alpha', '--json'])
    assert args.org == 'alpha' and args.json
    with pytest.raises(SystemExit) as forged:
        build_parser().parse_args(['workflows', *words, '--org', 'alpha', '--session-id', 'secret'])
    assert forged.value.code == 2


@pytest.mark.parametrize('bad', ['relative', 'malformed', 'array', 'utf8', 'identity', 'boolean'])
def test_activation_cli_parser_refusals_precede_transport(tmp_path, capsys, bad):
    path = tmp_path / 'activation.json'
    content = {'malformed': b'{', 'array': b'[]', 'utf8': b'\xff',
               'identity': b'{"task_id":"TASK-001"}',
               'boolean': b'{"expected_activation_revision":true}'}.get(bad, b'{}')
    path.write_bytes(content)
    with patch('cli.commands.workflows.OpcClient.from_env') as client:
        with pytest.raises(SystemExit) as refused:
            args = build_parser().parse_args([
                'workflows', 'activate', '--org', 'alpha', '--from-file',
                'relative.json' if bad == 'relative' else str(path),
            ])
            args.func(args)
        assert refused.value.code == 2
        client.assert_not_called()
    assert 'error' in capsys.readouterr().err


@pytest.mark.parametrize("format_row", ["legacy", "product", "proposal", "A", "Z"])
def test_activation_cli_real_http_create_replay_list_show_and_pending_exit(activation_org, tmp_path, capsys, monkeypatch, request, format_row):
    from tests.daemon.test_workflow_activation_routes import BASE, _snapshot
    client, org, state, body = activation_org
    if format_row != "legacy":
        client, org, state, cases = request.getfixturevalue("generic_activation_org")
        body = cases[format_row][0]
    path = tmp_path / 'activation.json'
    path.write_text(json.dumps(body))
    monkeypatch.setattr('cli.commands.workflows.OpcClient.from_env', lambda: client)
    words = ['workflows', 'activate', '--org', 'alpha', '--from-file', str(path), '--json']
    args = build_parser().parse_args(words)
    args.func(args)  # return means CLI main exits0, including pending execution
    first = json.loads(capsys.readouterr().out)
    assert first['pending'] is True and first['execution_started'] is False and first['replayed'] is False
    before = _snapshot(org)
    args.func(args)
    replay = json.loads(capsys.readouterr().out)
    assert replay['replayed'] is True and replay['root_task_id'] == first['root_task_id']
    assert _snapshot(org) == before
    for suffix in (['list'], ['show', first['activation_id']]):
        read = build_parser().parse_args(['workflows', 'activations', *suffix, '--org', 'alpha', '--json'])
        read.func(read)
        output = json.loads(capsys.readouterr().out)
        receipt = output[0] if isinstance(output, list) else output
        assert receipt['activation_id'] == first['activation_id'] and receipt['intent_id'] == first['intent_id']
    assert client.get(BASE + '/' + first['activation_id']).status_code == 200
    assert _snapshot(org) == before
    body['scope']['brief'] = 'changed same-key request'
    path.write_text(json.dumps(body))
    with pytest.raises(SystemExit) as conflict:
        args.func(args)
    assert conflict.value.code == 1
    assert 'workflow_activation_operation_conflict' in capsys.readouterr().err
    assert _snapshot(org) == before


@pytest.mark.parametrize("format_row", ["legacy", "product", "proposal", "A", "Z"])
@pytest.mark.parametrize('form', ['activate', 'list', 'show'])
def test_activation_cli_transport_errors_are_safe_and_nonzero(activation_org, tmp_path, capsys, form, request, format_row):
    import httpx
    _, _, _, body = activation_org
    if format_row != "legacy":
        _, _, _, cases = request.getfixturevalue("generic_activation_org")
        body = cases[format_row][0]
    path = tmp_path / 'activation.json'
    path.write_text(json.dumps(body))
    client = Mock()
    client.post.side_effect = client.get.side_effect = httpx.ConnectError('private transport details')
    words = (['activate', '--from-file', str(path)] if form == 'activate' else
             ['activations', form, *(['activation:one'] if form == 'show' else [])])
    args = build_parser().parse_args(['workflows', *words, '--org', 'alpha'])
    with patch('cli.commands.workflows.OpcClient.from_env', return_value=client), patch(
        'cli.commands.workflows._shared._fetch_available_orgs', return_value=['alpha'],
    ):
        with pytest.raises(SystemExit) as refused:
            args.func(args)
    assert refused.value.code == 1
    error = capsys.readouterr().err
    assert 'workflow activation transport failed' in error and 'private transport details' not in error


# Reuse the production-org fixture; no parallel mock authority fixture.
from tests.daemon.test_workflow_activation_routes import activation_org, generic_activation_org  # noqa: E402,F401


@pytest.mark.parametrize("vector", ["product", "proposal", "A", "Z"])
def test_generic_activation_cli_forwards_exact_finite_request(tmp_path, capsys, vector):
    body = {
        "format": "workflow-activation-request@2", "operation_key": "generic-1",
        "instance_id": "generic-one", "expected_activation_revision": 0,
        "template": {"identity_id": "published-id", "version": 2, "definition_digest": "a" * 64},
        "authority": {"namespace": "org/alpha", "generation": 1, "snapshot_digest": "b" * 64},
        "scope": {"brief": "Draft a bounded document."},
        "bindings": {
            "proposal-writer": {"kind": "agent", "principal": "dev_agent", "team": "engineering"},
            "sponsor": {"kind": "agent" if vector == "Z" else "human",
                        "principal": "qa_engineer" if vector == "Z" else "founder",
                        "team": "engineering" if vector == "Z" else None},
        },
        "eligible_replacements": {"proposal-writer": [], "sponsor": []},
        "allowed_actions": ["draft-document", "submit-immutable-document", "collect-review", "approve-planning-input"],
        "inputs": [],
    }
    if vector != "A":
        body["allowed_actions"].append("return-to-author")
    if vector == "product":
        body["bindings"] = {
            "product-lead": {"kind": "agent", "principal": "product_lead", "team": "product"},
            "founder": {"kind": "human", "principal": "founder", "team": None},
            "implementer": {"kind": "agent", "principal": "dev_agent", "team": "engineering"},
            "tester": {"kind": "agent", "principal": "qa_engineer", "team": "engineering"},
        }
        body["eligible_replacements"] = {role: [] for role in body["bindings"]}
    path = tmp_path / "generic-activation.json"
    path.write_text(json.dumps(body))
    client = Mock()
    client.post.return_value = _response(201, {"draft_only": True})
    with patch("cli.commands.workflows._founder_client", return_value=client), patch(
        "cli.commands.workflows._shared._fetch_available_orgs", return_value=["alpha"],
    ):
        args = build_parser().parse_args(["workflows", "activate", "--org", "alpha",
                                         "--from-file", str(path), "--json"])
        args.func(args)
    client.post.assert_called_once_with("/api/v1/orgs/alpha/workflows/activations", json=body)
    assert json.loads(capsys.readouterr().out) == {"draft_only": True}


@pytest.mark.parametrize("lane", ["founder", "agent"])
@pytest.mark.parametrize("bad", ["role", "timing", "outcomes", "schema"])
def test_generic_publish_timing_and_roles_refuse_before_both_client_lanes(tmp_path, capsys, lane, bad):
    import copy
    from tests.workflows.test_template_store import GENERIC_VECTORS
    definition = copy.deepcopy(GENERIC_VECTORS[1][1])
    if bad == "role":
        definition["author"]["role"] = "PRIVATE principal!"
    elif bad == "timing":
        definition["submission"]["timing"] = "PRIVATE always"
    elif bad == "outcomes":
        definition["outcomes"] = ["PRIVATE execute"]
    else:
        definition["schema_version"] = True
    path = tmp_path / "generic-template.json"
    path.write_text(json.dumps({"operation_key": "generic-publish", "template_name": "proposal",
                                "expected_current_version": 0, "definition": definition}))
    args = argparse.Namespace(from_file=str(path), session_id="sess-1" if lane == "agent" else None,
                              org="alpha", json=False)
    daemon_port = tmp_path / "port"
    daemon_port.write_text("9345")
    client = Mock()
    client.post.return_value = _response(201, {"namespace": "org/alpha/team/engineering",
        "template_name": "proposal", "version": 1, "definition_digest": "c" * 64})
    with patch("cli.commands.workflows._founder_client", return_value=client) as founder, patch(
        "cli.commands.workflows.httpx.Client", return_value=client
    ) as agent, patch("cli.commands.workflows.port_file", return_value=daemon_port) as port, patch(
        "cli.commands.workflows._shared._fetch_available_orgs", return_value=["alpha"],
    ):
        with pytest.raises(SystemExit) as refused:
            cmd_workflow_templates_publish(args)
        assert refused.value.code == 2
        founder.assert_not_called()
        agent.assert_not_called()
        port.assert_not_called()
    error = capsys.readouterr().err
    assert "invalid document-review template" in error
    assert "PRIVATE" not in error
