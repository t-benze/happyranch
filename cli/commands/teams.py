"""Founder team provisioning, saved online-move requests and registry reads."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import sys
import uuid

import httpx
from pydantic import ValidationError

from cli._shared import _ok
from cli.client.client import OpcClient
from cli.team_move_requests import PrivateMoveRequests, encoded, identifier
from runtime.infrastructure.team_move_store import CompletedReceipt, _bytes, _digest
from runtime.orchestrator.team_move_canonical import NEW_CHARTER
from runtime.orchestrator.team_move_public import ApplyMove, MoveSelection, PublicMoveRequest


def cmd_teams_list(args: argparse.Namespace) -> None:
    response = OpcClient.from_env().get(f"/api/v1/orgs/{args.org}/teams")
    if _ok(response):
        print(json.dumps(response.json(), indent=2))


def cmd_teams_ensure_default(args: argparse.Namespace) -> None:
    response = OpcClient.from_env().post(f"/api/v1/orgs/{args.org}/teams/ensure-default")
    if _ok(response):
        result = response.json()
        print(json.dumps(result, indent=2))
        if not result["authority"]["ready"]:
            raise SystemExit(1)


def _identifier(value):
    try:
        return identifier(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _revision(value):
    try:
        number = int(value)
        if not 0 < number < 2**63:
            raise ValueError
        return number
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive state revision") from None


def _preview_failure(phase, exc):
    category = "unknown"
    for classes, label in (
        ((httpx.TimeoutException,), "timeout"),
        ((httpx.HTTPError,), "http_error"),
        ((RuntimeError,), "runtime"),
        ((OSError,), "io"),
        ((ValidationError, ValueError), "validation_value"),
        ((KeyError,), "key"),
        ((TypeError,), "type"),
        ((SystemExit,), "exit"),
    ):
        if isinstance(exc, classes):
            category = label
            break
    try:
        print(f"Team-move preview failure: phase={phase} category={category}", file=sys.stderr)
    except Exception:
        pass  # A diagnostic sink failure must not replace the original failure.


@contextmanager
def _preview_client(phase):
    body_error = None
    try:
        with OpcClient.from_env() as client:
            try:
                yield client
            except BaseException as exc:
                body_error = exc
                _preview_failure(phase[0], exc)
                raise
            finally:
                phase[0] = "client_close"
    except BaseException as exc:
        if exc is not body_error:
            _preview_failure(phase[0], exc)
        raise


def _result(response, *, operation=False, expected_request_digest=None, preview_phase=None):
    if preview_phase is not None:
        preview_phase[0] = "json_decode"
    try:
        result = response.json()
    except ValueError:
        if preview_phase is not None:
            preview_phase[0] = "envelope_output"
        print(json.dumps({"http_status": response.status_code, "code": "team_move_invalid_response"}))
        if preview_phase is not None:
            preview_phase[0] = "json_decode"
        raise SystemExit(1) from None
    if preview_phase is not None:
        preview_phase[0] = "envelope_output"
    print(json.dumps({"http_status": response.status_code, "result": result}, indent=2))
    if response.status_code == 202:
        raise SystemExit(3)  # Accepted/in-flight, even if a misleading body says otherwise.
    if response.status_code != 200:
        raise SystemExit(1)
    if operation:
        try:
            if expected_request_digest is not None and result["request_digest"] != expected_request_digest:
                raise ValueError("saved operation belongs to a different normalized request")
            receipt = CompletedReceipt.model_validate_json(encoded(result["receipt"]))
            if (result["phase"] != "complete" or result["held"] is not False or result["committed"] is not True
                    or result["service_error"] is not None or result["operation_id"] != receipt.operation_id
                    or result["state_revision"] != receipt.state_revision
                    or result["private_request_digest"] != receipt.request_digest
                    or result["receipt_digest"] != _digest(_bytes(receipt))):
                raise ValueError("completion readback unavailable")
        except (KeyError, TypeError, ValueError):
            raise SystemExit(1) from None
    return result


def cmd_teams_move(args: argparse.Namespace) -> None:
    """All purely local validation/custody precedes client construction."""
    key = getattr(args, "operation_key", None)
    try:
        cache = PrivateMoveRequests(args.org)
        if args.move_command == "preview":
            key = key or "move_" + uuid.uuid4().hex
            selection = MoveSelection.model_validate_json(encoded({"version": 1,
                "source_team": args.source_team, "target_team": args.target_team,
                "affected_agents": args.agents, "demote": args.demote, "retire_source": args.retire_source,
                "charter": NEW_CHARTER, "drain_threads": args.drain_threads}))
            request_bytes = cache.save(key, "selection", selection.model_dump(mode="json"))
        elif args.move_command == "apply":
            request_bytes = cache.read(key, "apply")
            command = ApplyMove.model_validate_json(request_bytes)
            if command.operation_key != key or _digest(_bytes(command.request)) != args.expected_digest:
                raise ValueError("Saved key/request does not match the reviewed expected digest")
        elif args.move_command == "show":
            if (args.operation_id is None) == (key is None):
                raise ValueError("show requires exactly one operation ID or --operation-key")
    except (OSError, ValueError) as exc:
        print("Local team-move request refused: " + str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    if key is not None:
        print("operation_key=" + key, file=sys.stderr, flush=True)
    preview_phase = ["client_construction_entry"] if args.move_command == "preview" else None
    try:
        with (_preview_client(preview_phase) if preview_phase is not None else OpcClient.from_env()) as client:
            if args.move_command == "capability":
                response = client.team_move_capability(args.org)
                if response.status_code == 404:
                    print("Loaded service does not expose team-move capability; arrange release activation.", file=sys.stderr)
                _result(response)
            elif args.move_command == "preview":
                preview_phase[0] = "request_response"
                result = _result(client.team_move_preview(args.org, request_bytes), preview_phase=preview_phase)
                preview_phase[0] = "normalized_request_validation"
                normalized = PublicMoveRequest.model_validate_json(encoded(result["request"]))
                preview_phase[0] = "digest_inventory_consent_validation"
                if (result["inventory_complete"] is not True or
                        result["request_digest"] != _digest(_bytes(normalized)) or
                        normalized.drain_threads != selection.drain_threads):
                    raise ValueError("Preview request/inventory is unavailable; do not apply")
                preview_phase[0] = "apply_request_save"
                cache.save(key, "apply", ApplyMove(operation_key=key, request=normalized).model_dump(mode="json"))
                preview_phase[0] = "guidance_output"
                print(f"Saved normalized request. Review the exact diff, then use teams move apply --org {args.org} "
                      f"--operation-key {key} --expected-digest {result['request_digest']}", file=sys.stderr)
            elif args.move_command == "apply":
                try:
                    response = client.team_move_apply(args.org, request_bytes)
                except httpx.HTTPError:
                    print("POST response lost. Reading the SAME key once; no second apply or new key.", file=sys.stderr)
                    response = client.team_move_show(args.org, operation_key=key)
                _result(response, operation=True, expected_request_digest=_digest(_bytes(command.request)))
            elif args.move_command == "show":
                _result(client.team_move_show(args.org, operation_id=args.operation_id, operation_key=key), operation=True)
            elif args.move_command == "recover":
                _result(client.team_move_recover(args.org, args.operation_id, args.expected_revision), operation=True)
    except (httpx.HTTPError, RuntimeError, OSError, ValueError, KeyError, TypeError):
        print("Team-move transport/response unavailable. Preserve saved bytes; read capability and show the same key/ID. "
              "Do not generate another key or automatically repeat apply/recover.", file=sys.stderr)
        raise SystemExit(1) from None


def register(sub) -> None:
    parser = sub.add_parser("teams", help="Read teams, ensure Default or use founder-controlled team moves")
    commands = parser.add_subparsers(dest="teams_command", required=True)
    for name, handler, help_text in (
        ("list", cmd_teams_list, "Read the current team registry"),
        ("ensure-default", cmd_teams_ensure_default, "Provision an absent empty Default; preserve existing workers"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--org", required=True)
        command.set_defaults(func=handler)
    move = commands.add_parser("move", help="Founder move commands; read loaded capability and native readiness")
    operations = move.add_subparsers(dest="move_command", required=True)
    for name in ("capability", "preview", "apply", "show", "recover"):
        operation = operations.add_parser(name, help={"capability": "Read loaded capability and incomplete reasons",
            "preview": "Read exact native diff; save private normalized request/key without reserving",
            "apply": "Fsync saved key/body for an exact Founder-authorized move; requires compatible loading and native readiness",
            "show": "Read saved operation/receipt without progress or writes",
            "recover": "Explicit bounded progress of SAME operation/revision (gated)"}[name])
        operation.add_argument("--org", required=True, type=_identifier)
        operation.set_defaults(func=cmd_teams_move)
        if name in {"preview", "apply", "show"}:
            operation.add_argument("--operation-key", type=_identifier, required=name == "apply")
        if name == "preview":
            operation.add_argument("--from", dest="source_team", choices=["consultant"], required=True)
            operation.add_argument("--to", dest="target_team", choices=["default"], required=True)
            operation.add_argument("--agents", nargs=2, type=_identifier, required=True)
            operation.add_argument("--demote", type=_identifier, required=True)
            operation.add_argument("--retire-source", action="store_true", required=True)
            operation.add_argument("--drain-threads", action="store_true", help="Consent only to <=30s captured running thread drain")
        if name == "apply":
            operation.add_argument("--expected-digest", required=True, help="Exact request_digest displayed by the saved preview")
        if name in {"show", "recover"}:
            operation.add_argument("operation_id", type=_identifier, nargs="?" if name == "show" else None)
        if name == "recover":
            operation.add_argument("--expected-revision", type=_revision, required=True)
