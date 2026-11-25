"""Definition discovery and task values through the existing attribute API."""
from __future__ import annotations

import argparse
import json
import math
import re
from urllib.parse import quote
from typing import Any

import httpx

from cli import _shared
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def _local_error(key: str, reason: str) -> None:
    print(json.dumps({"detail": {"code": "attribute_validation_failed", "errors": {key: [reason]}}}))
    raise SystemExit(2)


def response_data(response: httpx.Response, *, emit: bool = False, machine: bool = False) -> dict:
    try:
        data = response.json()
    except ValueError:
        print(json.dumps({"detail": {"code": "invalid_response", "committed": None, "readback_required": True}}))
        raise SystemExit(1) from None
    if emit or not response.is_success:
        # Pretty text retains typed presence, original/unknown state, history
        # and refusal codes rather than inventing a second read projection.
        print(json.dumps(data, ensure_ascii=False, indent=None if machine else 2))
    if not response.is_success:
        raise SystemExit(1)
    return data


def session_query(session_id: str | None) -> dict:
    return {} if session_id is None else {"session_id": session_id}


def definitions(client: OpcClient, base: str, session_id: str | None, *, include_archived: bool = False) -> list[dict]:
    query = session_query(session_id)
    if include_archived:
        query["include_archived"] = "true"
    return response_data(client.get(base + "/task-attributes/definitions", params=query))["definitions"]


def select_definition(items: list[dict], selector: str) -> dict:
    for item in items:
        if selector in (item["key"], item["definition_id"]):
            return item
    _local_error(selector, "unknown_key_or_definition")


def scalar(definition: dict, text: str) -> Any:
    key = definition["key"]
    if definition["archived"]:
        _local_error(key, "archived_key")
    if definition["type"] == "string":
        return text
    if definition["type"] == "boolean":
        if text not in ("true", "false"):
            _local_error(key, "expected_true_or_false")
        return text == "true"
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text) is None:
        _local_error(key, "expected_finite_json_number")
    try:
        value = json.loads(text)
        if type(value) is float and not math.isfinite(value):
            _local_error(key, "expected_finite_json_number")
        return value
    except ValueError:
        _local_error(key, "expected_finite_json_number")


def assignments(tokens: list[str]) -> dict[str, str]:
    """Reject duplicates before building the value map; preserve the entire RHS."""
    result = {}
    for token in tokens:
        key, separator, value = token.partition("=")
        if not separator or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None:
            _local_error(key or "--attr", "expected_key_equals_value")
        if key in result:
            _local_error(key, "duplicate_key")
        result[key] = value
    return result


def creation_values(client: OpcClient, base: str, tokens: list[str], session_id: str | None) -> dict:
    raw = assignments(tokens)
    items = definitions(client, base, session_id, include_archived=True)
    return {key: scalar(select_definition(items, key), value) for key, value in raw.items()}


def transport_failure() -> None:
    print(json.dumps({"detail": {"code": "transport_unavailable", "committed": None,
                               "readback_required": True, "message": "Outcome unknown; read back before issuing another mutation"}}))
    raise SystemExit(1)


def cmd_attributes(args: argparse.Namespace) -> None:
    try:
        client = OpcClient.from_env()
        slug = _shared.resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
        base = f"/api/v1/orgs/{quote(slug, safe='')}"
        query = session_query(args.session_id)
        action = args.attribute_action
        if action == "list":
            if args.include_archived:
                query["include_archived"] = "true"
            response = client.get(base + "/task-attributes/definitions", params=query)
        elif action == "show":
            d = select_definition(definitions(client, base, args.session_id, include_archived=True), args.selector)
            response = client.get(base + "/task-attributes/definitions/" + quote(d["definition_id"], safe=""), params=query)
        else:
            url = base + "/tasks/" + quote(args.task_id, safe="") + "/attributes"
            if action == "get":
                response = client.get(url, params=query)
            elif action == "history":
                if args.key is not None:
                    d = select_definition(definitions(client, base, args.session_id, include_archived=True), args.key)
                    query["definition_id"] = d["definition_id"]
                response = client.get(url + "/history", params=query)
            else:
                d = select_definition(definitions(client, base, args.session_id, include_archived=True), args.key)
                if d["archived"]:
                    _local_error(d["key"], "archived_key")
                value = scalar(d, args.value) if action == "set" else None
                current = response_data(client.get(url, params=query))
                field = next((item for item in current["attributes"] if item["definition"]["definition_id"] == d["definition_id"]), None)
                change = {"definition_id": d["definition_id"], "expected_version": d["version"],
                          "expected_revision": field["current"]["revision"] if field else 0, "op": action}
                if action == "set":
                    change["value"] = value
                if args.note is not None:
                    change["note"] = args.note
                # Exactly one mutation, using the read preconditions. Never
                # reload/retry as Founder on conflicts or uncertain transport.
                response = client.request("PUT", url, params=query, json={"changes": [change]})
        response_data(response, emit=True, machine=args.json)
    except (DaemonNotRunning, DaemonStateInconsistent, httpx.HTTPError):
        transport_failure()


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("attributes", help="Discover task attribute definitions and read/edit task values")
    verbs = parser.add_subparsers(dest="attribute_action", required=True)
    for verb in ("list", "show", "get", "set", "clear", "history"):
        command = verbs.add_parser(verb)
        command.add_argument("--org", default=None)
        command.add_argument("--session-id", default=None, help="Current agent session proof; forwarded unchanged")
        command.add_argument("--json", action="store_true", help="Emit complete machine-readable API response")
        if verb == "list":
            command.add_argument("--include-archived", action="store_true")
        elif verb == "show":
            command.add_argument("selector", metavar="KEY_OR_ID")
        else:
            command.add_argument("task_id")
            if verb in ("set", "clear"):
                command.add_argument("key")
                command.add_argument("--note", default=None)
                if verb == "set":
                    command.add_argument("value")
            elif verb == "history":
                command.add_argument("--key", default=None, help="Current key or stable definition ID")
        command.set_defaults(func=cmd_attributes)
