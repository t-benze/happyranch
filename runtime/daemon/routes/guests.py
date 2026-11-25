"""Local guest MCP: JSON-RPC POST only, separate from all bearer routes."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import contextmanager
import json
import re
import secrets
import time
from typing import Iterator
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from runtime.daemon.auth import require_token, _require_human
from runtime.daemon.guest_setup import GUEST_RULES, setup_codex_guest
from runtime.daemon.org_state import OrgState, _OrgBusyError
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.thread_queue import ThreadJob
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.db.thread_guests import ThreadGuestError
from runtime.infrastructure.thread_archive import publish_thread_event
from runtime.identities.schema import NamingError
from runtime.models import ThreadMessageKind

router = APIRouter()
_LOCAL = {"127.0.0.1", "::1", "localhost"}
_STRING = {"type": "string"}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": required, "additionalProperties": False}}


TOOLS = [
    _tool("join_thread", "Redeem the founder's invite once; keep the returned token in this conversation.",
          {"thread_id": _STRING, "guest_id": _STRING, "invite_code": _STRING}, ["thread_id", "guest_id", "invite_code"]),
    _tool("read_thread", "Read labelled messages of your one thread, starting at your saved read cursor by default.",
          {"guest_token": _STRING, "since_seq": {"type": "integer", "minimum": 0},
           "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}, ["guest_token"]),
    _tool("post_message", "Post text to your one thread. Attachment tools are not available in this slice.",
          {"guest_token": _STRING, "body_markdown": _STRING}, ["guest_token", "body_markdown"]),
    _tool("wait_for_messages", "Wait for newer messages, up to 50 seconds. One wait at a time per seat.",
          {"guest_token": _STRING, "since_seq": {"type": "integer", "minimum": 0},
           "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 50}}, ["guest_token", "since_seq", "timeout_sec"]),
]


def _loopback(request: Request) -> None:
    if request.client is None or request.client.host not in _LOCAL:
        raise HTTPException(403, "loopback only")


def _mcp_gate(request: Request) -> None:
    _loopback(request)
    port = request.app.state.daemon.settings.daemon_port
    hosts = request.headers.getlist("host")
    try:
        parsed = urlsplit("http://" + (hosts[0] if len(hosts) == 1 else ""))
        valid = (parsed.hostname in _LOCAL and parsed.port == port and not parsed.username
                 and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment
                 and hosts[0].lower() in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"})
    except (ValueError, IndexError):
        valid = False
    if not valid or "origin" in request.headers:
        raise HTTPException(403, "invalid local request")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "application/json required")
    # No HTTP credential class is accepted here, including guest Authorization.
    if any(name in request.headers for name in ("authorization", "proxy-authorization", "x-api-key", "cookie")):
        raise HTTPException(401, "HTTP credentials are not accepted")


@contextmanager
def _org(request: Request, slug: str, original: OrgState | None = None) -> Iterator[OrgState]:
    state = request.app.state.daemon
    try:
        with state.capture_orgs((slug,), original=original) as orgs:
            if slug not in orgs:
                raise HTTPException(404, "unknown org")
            yield orgs[slug]
    except _OrgBusyError as exc:
        raise ThreadGuestError(400, "this guest seat has ended") from exc


def _arguments(name: object, args: object) -> dict:
    tool = next((tool for tool in TOOLS if tool["name"] == name), None)
    if tool is None or not isinstance(args, dict):
        raise ThreadGuestError(400, "invalid arguments")
    schema = tool["inputSchema"]
    if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
        raise ThreadGuestError(400, "invalid arguments")
    for key, value in args.items():
        prop = schema["properties"][key]
        if prop["type"] == "string":
            if not isinstance(value, str) or not value or (key != "body_markdown" and len(value) > 256):
                raise ThreadGuestError(400, "invalid arguments")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ThreadGuestError(400, "invalid arguments") from None
        elif type(value) is not int or not prop["minimum"] <= value <= prop.get("maximum", 2**63 - 1):
            raise ThreadGuestError(400, "invalid arguments")
    if "invite_code" in args and not args["invite_code"].startswith("hrinv_"):
        raise ThreadGuestError(400, "this invite is not valid")
    if "guest_token" in args and not args["guest_token"].startswith("hrguest_"):
        raise ThreadGuestError(400, "this guest seat has ended")
    return args


def _client_info(request: Request, supplied: object = None) -> dict:
    # Only bounded client name/version, never arbitrary metadata or credentials.
    source = supplied if isinstance(supplied, dict) else {"name": request.headers.get("user-agent", "unknown")}
    from runtime.daemon import paths
    master = paths.read_token()
    result = {}
    for key in ("name", "version"):
        value = source.get(key)
        if isinstance(value, str) and len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9 ._()/+-]+", value):
            if master and master in value:
                continue
            if not any(prefix in value.lower() for prefix in ("hrguest_", "hrinv_", "hrreg_", "bearer")):
                result[key] = value
    return result


async def _call(request: Request, slug: str, name: str, args: dict) -> dict:
    from runtime.daemon import paths
    master = paths.read_token()
    if master and any(args.get(key) == master for key in ("invite_code", "guest_token")):
        raise ThreadGuestError(400, "credential not accepted")
    with _org(request, slug) as org:
        if name == "join_thread":
            clients = getattr(request.app.state, "guest_clients", {})
            entry = clients.get(request.headers.get("mcp-session-id"))
            info = entry[1] if entry and time.monotonic() - entry[0] < 3600 else _client_info(request)
            result = org.db.join_thread_guest(**args, client_info=info)
            result["rules"] = GUEST_RULES
            tid = args["thread_id"]
            await publish_thread_event(org, slug, thread_id=tid, seq=result["last_seq"],
                speaker="guest:" + args["guest_id"], kind="system", preview="guest joined")
            return result
        token = args["guest_token"]
        seat = org.db.authenticate_thread_guest(token)
        if name == "read_thread":
            return org.db.read_thread_guest(token, args.get("since_seq"), args.get("limit", 50))
        if name == "post_message":
            tid = seat["thread_id"]
            speaker = f"guest:{seat['guest_id']}"
            recipients = [p.agent_name for p in org.db.list_thread_participants(tid)]
            try:
                seq, arrivals = org.db.record_conversational_arrival(thread_id=tid, speaker=speaker,
                    kind=ThreadMessageKind.MESSAGE, body_markdown=args["body_markdown"], recipients=recipients, guest_token=token)
            except ThreadGuestError as exc:
                if exc.code == "this guest seat has ended":
                    # If expiry crossed during naming capture, arrival rolled
                    # back. Persist the ending through its ordinary owner.
                    org.db.authenticate_thread_guest(token)
                raise
            org.db.increment_thread_turns_used(tid, by=1)
            AuditLogger(org.db).log_thread_message_sent(tid, seq=seq, speaker=speaker, kind="message")
            for arrival in arrivals:
                if arrival.invocation_token:
                    await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=arrival.invocation_token))
            await publish_thread_event(org, slug, thread_id=tid, seq=seq, speaker=speaker,
                kind="message", preview=args["body_markdown"])
            return {"seq": seq}
        waiters = getattr(request.app.state, "guest_waiters", None)
        if waiters is None:
            waiters = request.app.state.guest_waiters = set()
        key = (id(org), seat["guest_id"])
        if key in waiters:
            raise ThreadGuestError(400, "this guest seat already has a wait in progress")
        waiters.add(key)
    # No org reader, database lock, or SQLite transaction is held while asleep.
    deadline = time.monotonic() + args["timeout_sec"]
    try:
        while True:
            with _org(request, slug, original=org) as current:
                result = current.db.read_thread_guest(token, args["since_seq"])
            if result["messages"] or time.monotonic() >= deadline:
                return result
            await asyncio.sleep(min(1.0, max(0, deadline - time.monotonic())))
    finally:
        waiters.discard(key)


def _rpc_error(mid: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


@router.post("/guests/mcp", dependencies=[Depends(_mcp_gate)])
async def guest_mcp(slug: str, request: Request) -> Response:
    headers = {"Cache-Control": "no-store"}
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 262144:
            raise HTTPException(413, "request too large")
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return JSONResponse(_rpc_error(None, -32700, "parse error"), headers=headers)
    if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str)
            or ("id" in message and type(message["id"]) not in (str, int, type(None)))):
        return JSONResponse(_rpc_error(None, -32600, "invalid request"), headers=headers)
    mid, method, params = message.get("id"), message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return JSONResponse(_rpc_error(mid, -32602, "params must be an object"), headers=headers)
    if "id" not in message:
        return Response(status_code=202, headers=headers)
    if method == "initialize":
        clients = getattr(request.app.state, "guest_clients", None)
        if clients is None:
            clients = request.app.state.guest_clients = OrderedDict()
        while len(clients) >= 256:
            clients.popitem(last=False)
        session = secrets.token_hex(16)  # Metadata lookup only; grants no authority.
        clients[session] = (time.monotonic(), _client_info(request, params.get("clientInfo")))
        headers["Mcp-Session-Id"] = session
        version = params.get("protocolVersion", "2025-06-18")
        result = {"protocolVersion": version if isinstance(version, str) else "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "happyranch", "version": "1.0.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            name = params.get("name")
            args = _arguments(name, params.get("arguments", {}))
            data = await _call(request, slug, name, args)
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                      "structuredContent": data, "isError": False}
        except (ThreadGuestError, NamingError) as exc:
            text = exc.code if isinstance(exc, ThreadGuestError) else "message addresses are not valid"
            result = {"content": [{"type": "text", "text": text}], "isError": True}
    else:
        return JSONResponse(_rpc_error(mid, -32601, "method not found"), headers=headers)
    return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": result}, headers=headers)


@router.post("/guests/setup/{app_id}", dependencies=[require_token(), Depends(_require_human), Depends(_loopback)])
async def guest_setup(slug: str, app_id: str, org: OrgDep) -> dict:
    if app_id != "codex_desktop":
        raise HTTPException(422, "only codex_desktop is supported")
    try:
        result = setup_codex_guest(org.root, slug, org.settings.daemon_port)
    except (ValueError, OSError):
        raise HTTPException(409, "guest setup refused unsafe or edited project files") from None
    org.db.insert_audit_log(f"guests:{app_id}", "founder", "guest_setup", {"app_id": app_id})
    return result
