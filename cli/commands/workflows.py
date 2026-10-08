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
    if isinstance(definition, dict) and (definition.get("kind") == "document-review"
            or definition.get("schema_version") == 2):
        from runtime.workflows.templates import WorkflowTemplateError, _compile_document_definition
        try:
            _compile_document_definition(definition)
        except WorkflowTemplateError as exc:
            print("error: invalid document-review template", file=sys.stderr)
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


def register(sub: argparse._SubParsersAction) -> None:
    workflows = sub.add_parser("workflows", help="Publish templates and activate bounded workflow drafts")
    workflow_sub = workflows.add_subparsers(dest="workflows_command", required=True)
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
