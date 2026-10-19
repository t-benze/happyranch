"""Workflow-template CLI family."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from cli import _shared
from cli._shared import require_absolute_payload_path, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient
from runtime.runtime import port_file


def _read_payload(path: str) -> dict:
    absolute = require_absolute_payload_path(path, kind="workflow-template")
    try:
        value = json.loads(Path(absolute).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read workflow template payload: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if not isinstance(value, dict):
        print("error: workflow template payload must be a JSON object", file=sys.stderr)
        raise SystemExit(1)
    return value


def _print_error(response) -> None:
    code = None
    try:
        detail = response.json().get("detail")
        if isinstance(detail, dict):
            code = detail.get("code")
    except (AttributeError, ValueError):
        pass
    print(
        f"error ({response.status_code}): {code or response.text}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _print_publish(result: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, sort_keys=True))
        return
    print(
        f"published {result['namespace']}/{result['template_name']} "
        f"v{result['version']} digest={result['definition_digest']}"
    )


def _founder_client() -> OpcClient:
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_workflow_templates_publish(args: argparse.Namespace) -> None:
    body = _read_payload(args.from_file)
    definition = body.get("definition")
    if isinstance(definition, dict) and definition.get("schema_version") == 4:
        from runtime.workflows.artifacts import decode_artifact_json
        try:
            body = decode_artifact_json(Path(args.from_file).read_bytes())
        except (ValueError, UnicodeError) as exc:
            print("error: invalid artifact-review JSON", file=sys.stderr)
            raise SystemExit(2) from exc
        definition = body.get("definition")
    if isinstance(definition, dict) and (definition.get("kind") in ("document-review", "artifact-review")
            or definition.get("schema_version") in (2, 3, 4)):
        from runtime.workflows.templates import WorkflowTemplateError, _validate_definition
        try:
            _validate_definition(definition)
        except WorkflowTemplateError as exc:
            print("error: invalid review template", file=sys.stderr)
            raise SystemExit(2) from exc
    if args.session_id:
        slug = resolve_org_slug(args_org=args.org, available=[])
        port_path = port_file()
        if not port_path.exists():
            print("error: daemon not running — start it with scripts/daemon.sh start", file=sys.stderr)
            raise SystemExit(1)
        client = httpx.Client(
            base_url=f"http://127.0.0.1:{port_path.read_text().strip()}",
            headers={"X-HappyRanch-Surface": "cli"},
            timeout=30.0,
        )
        response = client.post(
            f"/api/v1/orgs/{slug}/workflows/templates/publish",
            json=body,
            params={"session_id": args.session_id},
        )
    else:
        client = _founder_client()
        slug = resolve_org_slug(
            args_org=args.org,
            available=_shared._fetch_available_orgs(client),
        )
        response = client.post(
            f"/api/v1/orgs/{slug}/workflows/templates/publish", json=body,
        )
    if response.status_code != 201:
        _print_error(response)
    _print_publish(response.json(), as_json=args.json)


def cmd_workflow_templates_list(args: argparse.Namespace) -> None:
    client = _founder_client()
    slug = resolve_org_slug(
        args_org=args.org,
        available=_shared._fetch_available_orgs(client),
    )
    response = client.get(
        f"/api/v1/orgs/{slug}/workflows/templates",
        params={"team_slug": args.team},
    )
    if response.status_code != 200:
        _print_error(response)
    result = response.json()
    if args.json:
        print(json.dumps(result, sort_keys=True))
        return
    for item in result["templates"]:
        print(
            f"{item['namespace']}/{item['template_name']} v{item['version']} "
            f"{item['definition_digest']}"
        )


def cmd_workflow_templates_show(args: argparse.Namespace) -> None:
    client = _founder_client()
    slug = resolve_org_slug(
        args_org=args.org,
        available=_shared._fetch_available_orgs(client),
    )
    response = client.get(
        f"/api/v1/orgs/{slug}/workflows/templates/"
        f"{args.team}/{args.template_name}/{args.version}"
    )
    if response.status_code != 200:
        _print_error(response)
    result = response.json()
    if args.json:
        print(json.dumps(result, sort_keys=True))
        return
    print(result["definition_json"])
    print(f"digest: {result['definition_digest']}")
    print(f"publisher: {json.dumps(result['publisher'], sort_keys=True)}")



def cmd_workflow_cutover(args: argparse.Namespace) -> None:
    body = None
    if args.cutover_command == "request":
        absolute = require_absolute_payload_path(args.from_file, kind="workflow-cutover")
        try:
            body = json.loads(Path(absolute).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            print("error: cannot read workflow cutover JSON payload", file=sys.stderr)
            raise SystemExit(1) from exc
        if not isinstance(body, dict):
            print("error: workflow cutover payload must be a JSON object", file=sys.stderr)
            raise SystemExit(1)
    try:
        client = _founder_client()
        slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
        base = f"/api/v1/orgs/{slug}/workflows/cutover"
        if args.cutover_command == "request":
            response = client.post(base + "/requests", json=body)
        elif args.cutover_command == "downgrade-preflight":
            response = client.get(base + "/downgrade-preflight")
        else:
            response = client.get(base)
    except httpx.HTTPError as exc:
        print("error: workflow cutover transport failed; retry requests with the same body/key", file=sys.stderr)
        raise SystemExit(1) from exc
    if response.status_code != 200:
        _print_error(response)
    result = response.json()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        projection = result.get("projection", result)
        print(f"{projection['org_slug']}: {projection['state']} generation={projection['generation']}")
        if "eligible" in result:
            print(f"downgrade eligible: {str(result['eligible']).lower()}")
        for blocker in result.get("blockers", []):
            print(f"{blocker['code']}: {blocker['owner']} — {blocker['required_action']}")
    if args.cutover_command == "downgrade-preflight" and not result["eligible"]:
        raise SystemExit(1)

def _activation_payload(path: str) -> dict:
    from runtime.workflows.activation import WorkflowActivationError, parse_request

    try:
        absolute = Path(path)
        if not absolute.is_absolute():
            raise ValueError("absolute path required")
        with absolute.open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("payload too large")
        body = json.loads(raw.decode("utf-8"))
        if isinstance(body, dict) and body.get('format') == 'workflow-activation-request@4':
            from runtime.workflows.artifacts import decode_artifact_json
            body = decode_artifact_json(raw)
        parse_request(body)
    except (OSError, UnicodeError, ValueError, WorkflowActivationError) as exc:
        print("error: activation requires an absolute --from-file path containing a valid closed JSON request", file=sys.stderr)
        raise SystemExit(2) from exc
    return body


def cmd_workflow_activation(args: argparse.Namespace) -> None:
    body = _activation_payload(args.from_file) if args.activation_command == "activate" else None
    try:
        client = _founder_client()
        slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
        base = f"/api/v1/orgs/{slug}/workflows/activations"
        if args.activation_command == "activate":
            response = client.post(base, json=body)
            valid_statuses = {200, 201}
        else:
            from urllib.parse import quote
            target = base if args.activation_command == "list" else base + "/" + quote(args.activation_id, safe="")
            response = client.get(target)
            valid_statuses = {200}
    except httpx.HTTPError as exc:
        print("error: workflow activation transport failed; retry activation with the same body/key", file=sys.stderr)
        raise SystemExit(1) from exc
    if response.status_code not in valid_statuses:
        _print_error(response)
    result = response.json()
    if args.json:
        print(json.dumps(result, sort_keys=True))
        return
    for receipt in result if isinstance(result, list) else [result]:
        print(f"{receipt['activation_id']}: {receipt['state']} root={receipt['root_task_id']} "
              f"execution_started={str(receipt['execution_started']).lower()} "
              f"pending={str(receipt['pending']).lower()} "
              f"reconciliation_required={str(receipt['reconciliation_required']).lower()}")
        for blocker in receipt['current_eligibility']['blockers']:
            print(f"{blocker}: responsible owner {receipt['responsible_owner']}")



def _handoff_payload(path: str) -> dict:
    from runtime.daemon.routes.workflow_submissions import decode_command
    from fastapi import HTTPException
    try:
        absolute = Path(path)
        if not absolute.is_absolute():
            raise ValueError("absolute path required")
        with absolute.open("rb") as source:
            raw = source.read(2097153)
        return decode_command(raw)
    except (OSError, ValueError, HTTPException) as exc:
        print("error: handoff requires an absolute --from-file path containing a closed JSON command", file=sys.stderr)
        raise SystemExit(2) from exc


def _workflow_token(value: str) -> str:
    import re
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) is None:
        raise argparse.ArgumentTypeError("expected a workflow token of 1..128 characters")
    return value


def cmd_workflow_handoff(args: argparse.Namespace) -> None:
    from urllib.parse import quote
    stage = args.handoff_command == "stage"
    body = _handoff_payload(args.from_file) if stage else None
    session_id = args.session_id
    if stage:
        params = {"session_id": session_id}
    elif args.handoff_command == "request-show":
        params = {"session_id": session_id} if session_id else {"proof_kind": "human"}
    elif session_id:
        if bool(args.activation_id) == bool(args.request_id):
            print("error: session proof requires exactly one --activation-id or --request-id", file=sys.stderr)
            raise SystemExit(2)
        params = {"session_id": session_id}
        params.update({"activation_id": args.activation_id} if args.activation_id else {"request_id": args.request_id})
    else:
        if args.activation_id or args.request_id:
            print("error: owner proof cannot include activation/request selectors", file=sys.stderr)
            raise SystemExit(2)
        params = {"proof_kind": "owner-human"}
    slug = resolve_org_slug(args_org=args.org, available=[])
    try:
        if session_id:
            # Reuse the existing header-free CLI session lane; no bearer custody.
            port_path = port_file()
            if not port_path.exists():
                raise DaemonNotRunning("daemon not running")
            client = httpx.Client(base_url=f"http://127.0.0.1:{port_path.read_text().strip()}",
                                  headers={"X-HappyRanch-Surface": "cli"}, timeout=30.0)
        else:
            client = _founder_client()
        base = f"/api/v1/orgs/{slug}/workflows"
        if stage:
            response = client.post(base + "/submissions/handoffs", params=params, json=body)
        else:
            target = ("/reviews/requests/" + quote(args.request_id, safe="")
                      if args.handoff_command == "request-show" else "/submissions/handoffs/" + quote(args.handoff_id, safe=""))
            response = client.get(base + target, params=params)
    except (httpx.HTTPError, OSError, DaemonNotRunning) as exc:
        print("error: workflow transport unavailable; retain the original operation key", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if session_id and 'client' in locals():
            client.close()
    if response.status_code not in ({200, 201} if stage else {200}):
        _print_error(response)
    result = response.json()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif args.handoff_command == "request-show":
        print(f"{result['request_id']}: {result['status']} signed={str(result['signed']).lower()} "
              f"pending={str(result['pending']).lower()}")
    else:
        current = result['current']
        print(f"{result['core']['handoff_id']}: {current['state']} {current['reason']} "
              f"pending={str(current['pending']).lower()} reconciliation_required={str(current['reconciliation_required']).lower()}")


def _human_review_payload(path: str) -> dict:
    from fastapi import HTTPException
    from runtime.daemon.routes.workflow_reviews import decode_human_command
    try:
        absolute = Path(path)
        if not absolute.is_absolute():
            raise ValueError('absolute path required')
        with absolute.open('rb') as source:
            return decode_human_command(source.read(2097153))
    except (OSError, ValueError, HTTPException) as exc:
        print('error: human review requires an absolute --from-file path containing a closed JSON command', file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_workflow_human_review(args: argparse.Namespace) -> None:
    body = _human_review_payload(args.from_file)
    slug = resolve_org_slug(args_org=args.org, available=[])
    try:
        client = _founder_client()
        response = client.post(f'/api/v1/orgs/{slug}/workflows/reviews/human-receipts', json=body)
    except httpx.HTTPError as exc:
        print('error: workflow review transport unavailable; retain the original operation key/body', file=sys.stderr)
        raise SystemExit(1) from exc
    if response.status_code != 200:
        _print_error(response)
    result = response.json()
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        receipt = result['receipt']
        print(f"{receipt['request_id']}: {receipt['outcome']} signed=true receipt={receipt['receipt_id']}")


def _review_stage_payload(path: str) -> dict:
    from fastapi import HTTPException
    from runtime.daemon.routes.workflow_reviews import decode_stage_command
    try:
        absolute = Path(path)
        if not absolute.is_absolute():
            raise ValueError('absolute path required')
        with absolute.open('rb') as source:
            return decode_stage_command(source.read(2097153))
    except (OSError, ValueError, HTTPException) as exc:
        print('error: review stage requires an absolute --from-file path containing a closed JSON command', file=sys.stderr)
        raise SystemExit(2) from exc


def cmd_workflow_review_stage(args: argparse.Namespace) -> None:
    from pydantic import ValidationError
    from runtime.daemon.routes.workflow_reviews import ReviewStageAck, ArtifactReviewStageAck, RevisionReviewStageAck
    body = _review_stage_payload(args.from_file)
    slug = resolve_org_slug(args_org=args.org, available=[])
    try:
        port_path = port_file()
        if not port_path.exists():
            raise DaemonNotRunning('daemon not running')
        with httpx.Client(base_url=f'http://127.0.0.1:{port_path.read_text().strip()}',
                          headers={'X-HappyRanch-Surface': 'cli'}, timeout=30.0) as client:
            response = client.post(f'/api/v1/orgs/{slug}/workflows/reviews/stages',
                                   params={'session_id': args.session_id}, json=body)
    except (httpx.HTTPError, OSError, DaemonNotRunning) as exc:
        print('error: review stage transport unavailable; retain the original operation key/body', file=sys.stderr)
        raise SystemExit(1) from exc
    if response.status_code != 200:
        _print_error(response)
    try:
        model = {'workflow-review-stage-command@1': ReviewStageAck, 'workflow-review-stage-command@2': ArtifactReviewStageAck,
                 'workflow-review-stage-command@3': RevisionReviewStageAck}[body['format']]
        result = model.model_validate(response.json()).model_dump()
        # A well-formed unrelated acknowledgement is still not this command.
        from runtime.workflows.draft_dispatch import canonical_bytes, digest
        if result['request_digest'] != digest(canonical_bytes(body)):
            raise ValueError('acknowledgement digest')
    except (ValueError, ValidationError) as exc:
        print('error: invalid unsigned stage acknowledgement; retain the original operation key/body', file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{body['request_id']}: staged signed=false replayed={str(result['replayed']).lower()} "
              f"stage_event_id={result['stage_event_id']}")



def cmd_workflow_revision(args: argparse.Namespace) -> None:
    from urllib.parse import quote
    from fastapi import HTTPException
    from pydantic import ValidationError
    from runtime.daemon.routes.workflow_revisions import decode_revision_command
    from runtime.workflows.revisions import DocumentRevisionReceipt
    slug = resolve_org_slug(args_org=args.org, available=[])
    action = args.revision_command
    body = None
    if action in ('start', 'stage'):
        try:
            absolute = Path(args.from_file)
            if not absolute.is_absolute():
                raise ValueError('absolute path required')
            with absolute.open('rb') as source:
                body = decode_revision_command(source.read(2097153), author=action == 'stage')
            if action == 'stage' and body['revision_operation_id'] != args.revision_operation_id:
                raise ValueError('path/body mismatch')
        except (OSError, ValueError, HTTPException) as exc:
            print('error: revision requires an absolute --from-file path containing a closed pinned command', file=sys.stderr)
            raise SystemExit(2) from exc
    base = f'/api/v1/orgs/{slug}/workflows'
    params = {'session_id': args.session_id} if getattr(args, 'session_id', None) else {'proof_kind': 'owner-human'}
    try:
        if action == 'stage' or (action == 'show' and getattr(args, 'session_id', None)):
            path = port_file()
            if not path.exists():
                raise DaemonNotRunning('daemon not running')
            client = httpx.Client(base_url=f'http://127.0.0.1:{path.read_text().strip()}',
                headers={'X-HappyRanch-Surface': 'cli'}, timeout=30.0)
        else:
            client = _founder_client()
        with client:
            if action == 'start':
                response = client.post(base + '/instances/' + quote(body['instance_id'], safe='') + '/revisions', json=body)
            elif action == 'stage':
                response = client.post(base + '/revisions/' + quote(args.revision_operation_id, safe='') + '/submissions', params=params, json=body)
            else:
                response = client.get(base + '/revisions/' + quote(args.revision_operation_id, safe=''), params=params)
    except (httpx.HTTPError, OSError, DaemonNotRunning) as exc:
        print('error: revision transport unavailable; retain the original operation key/body', file=sys.stderr)
        raise SystemExit(1) from exc
    if response.status_code not in (200, 201):
        _print_error(response)
    try:
        result = DocumentRevisionReceipt.model_validate(response.json()).model_dump()
        if action == 'start' and result['core']['instance_id'] != body['instance_id']:
            raise ValueError('receipt subject')
        if action != 'start' and result['core']['revision_operation_id'] != args.revision_operation_id:
            raise ValueError('receipt operation')
    except (ValueError, ValidationError) as exc:
        print('error: invalid revision receipt; retain the original operation key/body', file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['core']['revision_operation_id']}: {result['current']['state']} author_task={result['core']['author_task_id']} replayed={str(result['replayed']).lower()}")

def cmd_workflow_release_template(args: argparse.Namespace) -> None:
    from pydantic import ValidationError
    from runtime.workflows.release_templates import ReleaseTemplateReceipt

    try:
        with _founder_client() as client:
            slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
            response = client.get(
                f"/api/v1/orgs/{slug}/workflows/release-templates/{args.kind}/{args.version}"
            )
    except httpx.HTTPError as exc:
        print("error: release catalog transport unavailable", file=sys.stderr)
        raise SystemExit(1) from exc
    if response.status_code != 200:
        _print_error(response)
    try:
        result = ReleaseTemplateReceipt.model_validate(response.json()).model_dump()
    except (ValueError, ValidationError) as exc:
        print("error: invalid disabled release catalog receipt", file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"{result['identity']} v{result['version']} digest={result['definition_digest']} "
              "enabled=false reason=release_catalog_only")



def cmd_workflow_coding_release_import(args: argparse.Namespace) -> None:
    import hashlib
    from runtime.workflows._coding_release_history import COMMAND_LIMIT, parse_import_command
    from runtime.workflows.coding_release_import import ImportReceipt
    raw = None
    command = None
    try:
        if args.coding_command == 'release-import':
            absolute = require_absolute_payload_path(args.from_file, kind='coding-release-import')
            with Path(absolute).open('rb') as source:
                raw = source.read(COMMAND_LIMIT + 1)
            command = parse_import_command(raw)
        with _founder_client() as client:
            slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
            path = f'/api/v1/orgs/{slug}/workflows/release-templates/coding/1/import'
            if raw is None:
                response = client.get(path)
            else:
                # Human-only transport, complete original bytes and stable key.
                # Transport loss preserves the file; never issue another POST.
                response = client.post(path, content=raw, headers={'Content-Type': 'application/json'})
        if response.status_code not in ((200,) if raw is None else (200, 201)):
            _print_error(response)
        receipt = ImportReceipt.model_validate(response.json()).model_dump()
        if command is not None and (receipt['operation_key'] != command['operation_key']
                or receipt['request_digest'] != hashlib.sha256(raw).hexdigest()
                or receipt['replayed'] != (response.status_code == 200)):
            raise ValueError('coding_release_import_receipt_mismatch')
        if command is None and receipt['replayed']:
            raise ValueError('coding_release_import_receipt_mismatch')
    except (OSError, ValueError, httpx.HTTPError):
        print('error: coding release import command, transport or receipt unavailable; preserve original file/key',
              file=sys.stderr)
        raise SystemExit(1) from None
    if args.json:
        print(json.dumps(receipt, sort_keys=True))
    else:
        print(f"{receipt['version_id']} current={receipt['current']['state']} "
              f"replayed={str(receipt['replayed']).lower()} coding-enabled=false")


def cmd_workflow_coding_roles(args: argparse.Namespace) -> None:
    from runtime.workflows._coding_role_records import (
        COMMAND_LIMIT, RoleReceipt, SelectionReceipt, parse_role_command, parse_selection,
    )
    from runtime.workflows.coding_saved_records import _canonical, _hash
    slug = resolve_org_slug(args_org=args.org, available=[])
    form = args.coding_command
    command, raw = None, None
    try:
        if form != 'mapping-show':
            absolute = require_absolute_payload_path(args.from_file, kind='coding-roles')
            with Path(absolute).open('rb') as stream:
                raw = stream.read(COMMAND_LIMIT + 1)
            command = (parse_selection(raw) if form == 'role-select' else
                       parse_role_command(raw, org_slug=slug))
        path = port_file()
        if not path.exists():
            raise DaemonNotRunning('daemon not running')
        endpoint = 'role-selections' if form == 'role-select' else 'mappings'
        with httpx.Client(base_url=f'http://127.0.0.1:{path.read_text().strip()}',
                headers={'X-HappyRanch-Surface': 'cli'}, timeout=30.0) as client:
            url = f'/api/v1/orgs/{slug}/workflows/coding/{endpoint}'
            if form == 'mapping-show':
                response = client.get(url, params={'session_id': args.session_id})
            else:
                response = client.post(url, params={'session_id': args.session_id},
                    content=raw, headers={'Content-Type': 'application/json'})
        if response.status_code not in (200, 201):
            _print_error(response)
        model = SelectionReceipt if form == 'role-select' else RoleReceipt
        receipt = model.model_validate(response.json()).model_dump()
        if form == 'role-select':
            if receipt['mapping'] != command['mapping'] or receipt['subject'] != command['subject']:
                raise ValueError('coding_role_selection_mismatch')
            if (receipt['overridden'] != (command['implementer_override'] is not None)
                    or (command['implementer_override'] is not None
                        and receipt['roles']['implementer'] != command['implementer_override'])
                    or len({role['principal'] for role in receipt['roles'].values()}) != 4):
                raise ValueError('coding_role_selection_mismatch')
        else:
            mapping = receipt['mapping']
            if mapping['authority']['namespace'] != 'org/' + slug:
                raise ValueError('coding_role_receipt_mismatch')
            for reference in mapping['repository_references']:
                if reference['repository_id'] != 'workflow-repository:' + _hash(_canonical(
                        [slug, reference['repo_key'], reference['origin_digest']])):
                    raise ValueError('coding_role_receipt_mismatch')
            if command is not None and (mapping != command['mapping']
                    or receipt['generation'] != command['expected_generation'] + 1):
                raise ValueError('coding_role_receipt_mismatch')
    except (ValueError, OSError, httpx.HTTPError, DaemonNotRunning) as exc:
        print('error: coding roles command, transport or receipt unavailable; preserve original file/key',
              file=sys.stderr)
        raise SystemExit(1) from exc
    if args.json:
        print(json.dumps(receipt, sort_keys=True))
    else:
        print(json.dumps(receipt, sort_keys=True))


def register(sub: argparse._SubParsersAction) -> None:
    workflows = sub.add_parser("workflows", help="Publish templates and activate bounded workflow drafts")
    workflow_sub = workflows.add_subparsers(dest="workflows_command", required=True)
    coding = workflow_sub.add_parser('coding', help='Fixed release import and agent-manager role defaults; no coding run allocation')
    coding_sub = coding.add_subparsers(dest='coding_command', required=True)
    for form in ('mapping-publish', 'mapping-show', 'role-select'):
        command = coding_sub.add_parser(form, help='Agent-manager role defaults or nonallocating pinned selection')
        command.add_argument('--org')
        command.add_argument('--session-id', required=True)
        command.add_argument('--json', action='store_true')
        if form != 'mapping-show':
            command.add_argument('--from-file', required=True)
        command.set_defaults(func=cmd_workflow_coding_roles)
    for form in ('release-import', 'release-import-show'):
        command = coding_sub.add_parser(form, help='Explicit fixed coding v1 import or saved receipt')
        command.add_argument('--org', required=True)
        command.add_argument('--json', action='store_true')
        if form == 'release-import':
            command.add_argument('--from-file', required=True)
        command.set_defaults(func=cmd_workflow_coding_release_import)
    release = workflow_sub.add_parser("release-template", help="Read the disabled release catalog")
    release.add_argument("kind", choices=["coding"])
    release.add_argument("--version", required=True, type=int, choices=[1])
    release.add_argument("--org")
    release.add_argument("--json", action="store_true")
    release.set_defaults(func=cmd_workflow_release_template)
    revisions = workflow_sub.add_parser('revisions', help='Explicit owner return, safe receipt and new-author r2 staging')
    revision_sub = revisions.add_subparsers(dest='revision_command', required=True)
    for action in ('start', 'show', 'stage'):
        command = revision_sub.add_parser(action)
        command.add_argument('--org', required=True)
        command.add_argument('--json', action='store_true')
        if action != 'start':
            command.add_argument('revision_operation_id', type=_workflow_token)
        if action in ('start', 'stage'):
            command.add_argument('--from-file', required=True)
        if action == 'stage':
            command.add_argument('--session-id', required=True, type=_workflow_token)
        elif action == 'show':
            proof = command.add_mutually_exclusive_group(required=True)
            proof.add_argument('--session-id', type=_workflow_token)
            proof.add_argument('--proof-kind', choices=['owner-human'])
        command.set_defaults(func=cmd_workflow_revision)
    reviews = workflow_sub.add_parser('reviews', help='Stage current reviewer findings or sign a human request')
    review_sub = reviews.add_subparsers(dest='review_command', required=True)
    sign = review_sub.add_parser('sign', help='Submit a Founder-bearer human receipt')
    sign.add_argument('--org', required=True)
    sign.add_argument('--from-file', required=True)
    sign.add_argument('--json', action='store_true')
    sign.set_defaults(func=cmd_workflow_human_review)
    stage_review = review_sub.add_parser('stage', help='Stage current agent findings before the genuine final callback')
    stage_review.add_argument('--org', required=True)
    stage_review.add_argument('--session-id', required=True, type=_workflow_token)
    stage_review.add_argument('--from-file', required=True)
    stage_review.add_argument('--json', action='store_true')
    stage_review.set_defaults(func=cmd_workflow_review_stage)
    submissions = workflow_sub.add_parser("submissions", help="Stage original-current output and read safe handoff receipts")
    handoff_sub = submissions.add_subparsers(dest="handoff_command", required=True)
    for form in ("stage", "show", "request-show"):
        command = handoff_sub.add_parser(form)
        command.add_argument("--org", required=True)
        command.add_argument("--json", action="store_true")
        if form == "stage":
            command.add_argument("--from-file", required=True)
            command.add_argument("--session-id", required=True, type=_workflow_token)
        else:
            command.add_argument("handoff_id" if form == "show" else "request_id", type=_workflow_token)
            lane = command.add_mutually_exclusive_group(required=True)
            lane.add_argument("--session-id", type=_workflow_token)
            lane.add_argument("--proof-kind", choices=["owner-human" if form == "show" else "human"])
            if form == "show":
                selector = command.add_mutually_exclusive_group()
                selector.add_argument("--activation-id", type=_workflow_token)
                selector.add_argument("--request-id", type=_workflow_token)
        command.set_defaults(func=cmd_workflow_handoff)

    activation = workflow_sub.add_parser("activate", help="Founder activation of an exact template version")
    activation.add_argument("--org", required=True)
    activation.add_argument("--from-file", required=True)
    activation.add_argument("--json", action="store_true")
    activation.set_defaults(func=cmd_workflow_activation, activation_command="activate")
    activations = workflow_sub.add_parser("activations", help="Read original activation receipts and current eligibility")
    activation_sub = activations.add_subparsers(dest="activation_command", required=True)
    for form in ("list", "show"):
        command = activation_sub.add_parser(form)
        command.add_argument("--org", required=True)
        command.add_argument("--json", action="store_true")
        if form == "show":
            command.add_argument("activation_id")
        command.set_defaults(func=cmd_workflow_activation)
    cutover = workflow_sub.add_parser("cutover", help="Request and inspect workflow cutover")
    cutover_sub = cutover.add_subparsers(dest="cutover_command", required=True)
    for form in ("show", "request", "downgrade-preflight"):
        command = cutover_sub.add_parser(form)
        command.add_argument("--org", required=True)
        command.add_argument("--json", action="store_true")
        if form == "request":
            command.add_argument("--from-file", required=True)
        command.set_defaults(func=cmd_workflow_cutover)
    templates = workflow_sub.add_parser("templates", help="Publish and read workflow templates")
    template_sub = templates.add_subparsers(dest="templates_command", required=True)

    publish = template_sub.add_parser("publish", help="Publish one immutable template version")
    publish.add_argument("--from-file", required=True)
    publish.add_argument("--session-id")
    publish.add_argument("--org")
    publish.add_argument("--json", action="store_true")
    publish.set_defaults(func=cmd_workflow_templates_publish)

    listing = template_sub.add_parser("list", help="List immutable template versions")
    listing.add_argument("--team", required=True)
    listing.add_argument("--org")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_workflow_templates_list)

    show = template_sub.add_parser("show", help="Show one immutable template version")
    show.add_argument("team")
    show.add_argument("template_name")
    show.add_argument("version", type=int)
    show.add_argument("--org")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_workflow_templates_show)
