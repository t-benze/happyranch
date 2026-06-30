"""Thread operations (compose, reply, decline, dispatch, forward, ...)."""
from __future__ import annotations

import argparse
import mimetypes
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import OpcClient

_SAFE_ARTIFACT_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_artifact_basename(path: Path) -> str:
    cleaned = _SAFE_ARTIFACT_CHARS.sub("-", path.name).strip(".-")
    return cleaned or "attachment.bin"


def _artifact_name_for_attach(
    thread_id: str | None,
    path: Path,
    *,
    collision_index: int = 1,
) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = thread_id or "thread-draft"
    suffix = f"{collision_index}-" if collision_index > 1 else ""
    return f"{prefix}-{stamp}-{suffix}{_safe_artifact_basename(path)}"


def _merge_uploaded_attachments(
    *,
    client: OpcClient,
    slug: str,
    payload: dict,
    attach_paths: list[Path] | None,
    agent: str,
    thread_id: str | None,
) -> dict:
    refs = list(payload.get("attachments") or [])
    generated_names: dict[str, int] = {}
    for path in attach_paths or []:
        artifact_name = _artifact_name_for_attach(thread_id, path)
        generated_names[artifact_name] = generated_names.get(artifact_name, 0) + 1
        if generated_names[artifact_name] > 1:
            artifact_name = _artifact_name_for_attach(
                thread_id,
                path,
                collision_index=generated_names[artifact_name],
            )
        info = client.put_artifact(
            slug=slug,
            local_path=path,
            name=artifact_name,
            agent=agent,
        )
        refs.append({
            "artifact_name": info["name"],
            "display_name": path.name,
            "content_type": mimetypes.guess_type(path.name)[0],
        })
    if refs or attach_paths:
        payload["attachments"] = refs
    return payload


def cmd_threads_tui(args: argparse.Namespace) -> None:
    """Stub left in place for `happyranch threads` (no subcommand).

    The Textual TUI was removed in favor of the web UI. This handler now
    prints a one-liner pointing the founder at `happyranch web` and exits 0 so
    muscle memory typing `happyranch threads` doesn't error.
    """
    del args  # unused
    print("happyranch threads — the TUI was removed. Use `happyranch web` for the threads inbox.")
    print("CLI subcommands (compose, list, show, send, …) still work — see `happyranch threads --help`.")



def cmd_threads_compose(args: argparse.Namespace) -> None:
    import json as _json
    import sys
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    # Agent-initiated compose: requires --from-file with a JSON payload that
    # includes `composer` + task binding flags supplied on the CLI.
    if getattr(args, "task_id", None):
        if not args.from_file:
            print(
                "error: --from-file is required for agent-initiated compose",
                file=sys.stderr,
            )
            sys.exit(2)
        with open(args.from_file) as fh:
            payload = _json.load(fh)
        payload["task_id"] = args.task_id
        if args.session_id:
            payload["session_id"] = args.session_id
        payload = _merge_uploaded_attachments(
            client=client,
            slug=slug,
            payload=payload,
            attach_paths=getattr(args, "attach", None),
            agent=payload.get("composer", ""),
            thread_id=None,
        )
        r = client.post(
            f"/api/v1/orgs/{slug}/threads/compose-as-agent", json=payload,
        )
        if not _ok(r):
            return
        body = r.json()
        print(
            f"{body['thread_id']}  started={_fmt_ts(body['started_at'])}  "
            f"composed_by={body['composed_by']}  "
            f"pending={body['pending_replies']}"
        )
        return

    # Founder path — unchanged.
    if not (args.subject and args.recipients and (args.body or getattr(args, "attach", None))):
        print(
            "error: --subject, --recipients, and --body or --attach required for founder compose",
            file=sys.stderr,
        )
        sys.exit(2)
    recipients = [r.strip() for r in args.recipients.split(",") if r.strip()]
    payload = {
        "subject": args.subject,
        "recipients": recipients,
        "body_markdown": args.body or "",
    }
    payload = _merge_uploaded_attachments(
        client=client,
        slug=slug,
        payload=payload,
        attach_paths=getattr(args, "attach", None),
        agent="founder",
        thread_id=None,
    )
    r = client.post(f"/api/v1/orgs/{slug}/threads", json=payload)
    if not _ok(r):
        return
    body = r.json()
    print(
        f"{body['thread_id']}  started={_fmt_ts(body['started_at'])}  "
        f"pending={body['pending_replies']}"
    )



def cmd_threads_list(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    params: dict = {"limit": args.limit}
    if args.status:
        params["status"] = args.status
    r = client.get(f"/api/v1/orgs/{slug}/threads", params=params)
    if not _ok(r):
        return
    for t in r.json()["threads"]:
        print(
            f"{t['thread_id']:10s}  {t['status']:12s}  "
            f"turns={t['turns_used']}/{t['turn_cap']}  "
            f"{_fmt_ts(t['started_at'])}  {t['subject'][:60]}"
        )



def cmd_threads_reply(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    try:
        body = _json.loads(Path(args.from_file).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    thread_id = args.thread_id or body.get("thread_id", "")
    body = _merge_uploaded_attachments(
        client=client,
        slug=slug,
        payload=body,
        attach_paths=getattr(args, "attach", None),
        agent=body.get("speaker", ""),
        thread_id=thread_id,
    )
    r = client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/reply", json=body)
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: reply seq={resp['seq']} on {resp['thread_id']}")



def cmd_threads_decline(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    try:
        body = _json.loads(Path(args.from_file).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    thread_id = args.thread_id or body.get("thread_id", "")
    r = client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/decline", json=body)
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: decline seq={resp['seq']} on {resp['thread_id']}")



def cmd_threads_dispatch(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    try:
        body = _json.loads(Path(args.from_file).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    thread_id = args.thread_id or body.get("thread_id", "")
    r = client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/dispatch", json=body)
    if not _ok(r):
        return
    resp = r.json()
    superseded = resp.get("superseded_task_id")
    if superseded:
        print(
            f"ok: dispatched {resp['task_id']} from"
            f" {resp['dispatched_from_thread_id']}"
            f" -> supersedes {superseded}"
        )
    else:
        print(
            f"ok: dispatched {resp['task_id']} from"
            f" {resp['dispatched_from_thread_id']}"
        )



def cmd_threads_show(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{slug}/threads/{args.thread_id}")
    if not _ok(r):
        return
    data = r.json()
    if args.json:
        print(_json.dumps(data, indent=2))
        return
    print(f"Thread: {data['thread_id']} — {data['subject']}")
    print(f"  status: {data['status']}  turns: {data['turns_used']}/{data['turn_cap']}")
    print(f"  participants: {', '.join(data.get('participants', []))}")
    if data.get("forwarded_from_id"):
        print(f"  forwarded from: {data['forwarded_from_id']}")
    print()
    for m in data.get("messages", []):
        kind = m["kind"]
        head = f"--- seq {m['seq']} — {m['speaker']} · {kind}"
        print(head)
        if m.get("body_markdown"):
            print(m["body_markdown"])
        elif m.get("decline_reason"):
            print(f"  declined: {m['decline_reason']}")
        elif m.get("system_payload"):
            print(f"  system: {m['system_payload']}")
        for attachment in m.get("attachments", []):
            size = attachment.get("size_bytes")
            size_text = f" ({size}B)" if size is not None else ""
            print(
                f"  attachment: {attachment['display_name']} "
                f"[artifact:{attachment['artifact_name']}]{size_text}"
            )
        print()



def cmd_threads_send(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    try:
        payload = _json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    payload = _merge_uploaded_attachments(
        client=client,
        slug=slug,
        payload=payload,
        attach_paths=getattr(args, "attach", None),
        agent="founder",
        thread_id=args.thread_id,
    )
    r = client.post(f"/api/v1/orgs/{slug}/threads/{args.thread_id}/send", json=payload)
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))



def cmd_threads_invite(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/threads/{args.thread_id}/invite",
        json={"agent_name": args.agent},
    )
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))



def cmd_threads_extend(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/threads/{args.thread_id}/extend",
        json={"new_cap": args.new_cap},
    )
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))



def cmd_threads_archive(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    try:
        payload = _json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    r = client.post(
        f"/api/v1/orgs/{slug}/threads/{args.thread_id}/archive", json=payload,
    )
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))



def cmd_threads_resume(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/threads/{args.thread_id}/resume",
    )
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))



def cmd_threads_abort_replies(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/threads/{args.thread_id}/abort-replies",
    )
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))


def cmd_threads_forward(args: argparse.Namespace) -> None:
    import json as _json
    from datetime import datetime
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    note = Path(args.note_file).read_text(encoding="utf-8") if args.note_file else ""
    source = args.source
    if source.startswith("THR-"):
        from cli.thread_forward import build_forward_body_from_thread
        from runtime.models import ThreadMessage, ThreadMessageKind
        thr_resp = client.get(f"/api/v1/orgs/{slug}/threads/{source}")
        if not _ok(thr_resp):
            return
        thr = thr_resp.json()
        msgs = [
            ThreadMessage(
                thread_id=source, seq=m["seq"], speaker=m["speaker"],
                kind=ThreadMessageKind(m["kind"]),
                body_markdown=m.get("body_markdown"),
                decline_reason=m.get("decline_reason"),
                system_payload=m.get("system_payload"),
                created_at=datetime.fromisoformat(m["created_at"]),
            )
            for m in thr.get("messages", [])
        ]
        quoted = build_forward_body_from_thread(
            source_id=source, messages=msgs, subject=thr["subject"],
        )
        kind = "thread"
        default_subject = f"Fwd: {thr['subject']}"
    else:
        print("error: --source must start with THR-")
        sys.exit(2)

    body = quoted + note
    payload = {
        "subject": args.subject or default_subject,
        "recipients": [r.strip() for r in args.recipients.split(",") if r.strip()],
        "body_markdown": body,
        "forwarded_from_id": source,
        "forwarded_from_kind": kind,
    }
    r = client.post(f"/api/v1/orgs/{slug}/threads", json=payload)
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))



def register(sub) -> None:
    p_threads = sub.add_parser("threads", help="Thread operations (compose, reply, decline, dispatch)")
    p_threads.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_threads.set_defaults(func=cmd_threads_tui)
    threads_sub = p_threads.add_subparsers(dest="threads_command", required=False)

    p_threads_compose = threads_sub.add_parser(
        "compose", help="Compose a new thread (founder direct or agent-initiated)",
    )
    p_threads_compose.add_argument("--org", default=None, help="Org slug")
    p_threads_compose.add_argument(
        "--from-file", default=None, dest="from_file",
        help="JSON payload (required for agent-initiated compose)",
    )
    p_threads_compose.add_argument(
        "--task-id", default=None, dest="task_id",
        help="Active task binding for agent-initiated compose",
    )
    p_threads_compose.add_argument(
        "--session-id", default=None, dest="session_id",
        help="Active session id (required with --task-id)",
    )
    # Legacy founder-direct flags (still supported, no --from-file needed):
    p_threads_compose.add_argument("--subject", default=None)
    p_threads_compose.add_argument(
        "--recipients", default=None,
        help="Comma-separated agent names (founder path)",
    )
    p_threads_compose.add_argument(
        "--body", default=None,
        help="Opening message body (founder path)",
    )
    p_threads_compose.add_argument("--attach", action="append", type=Path, default=None)
    p_threads_compose.set_defaults(func=cmd_threads_compose)

    p_threads_list = threads_sub.add_parser("list", help="List threads")
    p_threads_list.add_argument("--org", default=None, help="Org slug")
    p_threads_list.add_argument("--status", default=None, help="Filter by status (open|archived)")
    p_threads_list.add_argument("--limit", type=int, default=50)
    p_threads_list.set_defaults(func=cmd_threads_list)

    p_threads_reply = threads_sub.add_parser("reply", help="Agent callback: post a reply to a thread")
    p_threads_reply.add_argument("--org", default=None, help="Org slug")
    p_threads_reply.add_argument("--thread-id", dest="thread_id", default=None)
    p_threads_reply.add_argument("--from-file", required=True)
    p_threads_reply.add_argument("--attach", action="append", type=Path, default=None)
    p_threads_reply.set_defaults(func=cmd_threads_reply)

    p_threads_decline = threads_sub.add_parser("decline", help="Agent callback: decline a thread turn")
    p_threads_decline.add_argument("--org", default=None, help="Org slug")
    p_threads_decline.add_argument("--thread-id", dest="thread_id", default=None)
    p_threads_decline.add_argument("--from-file", required=True)
    p_threads_decline.set_defaults(func=cmd_threads_decline)

    p_threads_dispatch = threads_sub.add_parser("dispatch", help="Agent callback: dispatch a task from a thread")
    p_threads_dispatch.add_argument("--org", default=None, help="Org slug")
    p_threads_dispatch.add_argument("--thread-id", dest="thread_id", default=None)
    p_threads_dispatch.add_argument("--from-file", required=True)
    p_threads_dispatch.set_defaults(func=cmd_threads_dispatch)

    p_threads_show = threads_sub.add_parser("show", help="Show a thread's metadata + transcript")
    p_threads_show.add_argument("--org", default=None, help="Org slug")
    p_threads_show.add_argument("thread_id")
    p_threads_show.add_argument("--json", action="store_true")
    p_threads_show.set_defaults(func=cmd_threads_show)

    p_threads_send = threads_sub.add_parser("send", help="Founder: send a follow-up message to a thread")
    p_threads_send.add_argument("--org", default=None, help="Org slug")
    p_threads_send.add_argument("--thread-id", dest="thread_id", required=True)
    p_threads_send.add_argument("--from-file", dest="from_file", required=True)
    p_threads_send.add_argument("--attach", action="append", type=Path, default=None)
    p_threads_send.set_defaults(func=cmd_threads_send)

    p_threads_invite = threads_sub.add_parser("invite", help="Founder: invite a participant to a thread")
    p_threads_invite.add_argument("--org", default=None, help="Org slug")
    p_threads_invite.add_argument("--thread-id", dest="thread_id", required=True)
    p_threads_invite.add_argument("--agent", required=True)
    p_threads_invite.set_defaults(func=cmd_threads_invite)

    p_threads_extend = threads_sub.add_parser("extend", help="Founder: raise a thread's turn cap")
    p_threads_extend.add_argument("--org", default=None, help="Org slug")
    p_threads_extend.add_argument("--thread-id", dest="thread_id", required=True)
    p_threads_extend.add_argument("--new-cap", dest="new_cap", type=int, required=True)
    p_threads_extend.set_defaults(func=cmd_threads_extend)

    p_threads_archive = threads_sub.add_parser("archive", help="Founder: archive a thread (Phase A -> B)")
    p_threads_archive.add_argument("--org", default=None, help="Org slug")
    p_threads_archive.add_argument("--thread-id", dest="thread_id", required=True)
    p_threads_archive.add_argument("--from-file", dest="from_file", required=True)
    p_threads_archive.set_defaults(func=cmd_threads_archive)

    p_threads_resume = threads_sub.add_parser(
        "resume", help="Founder: reopen an archived thread",
    )
    p_threads_resume.add_argument("--org", default=None, help="Org slug")
    p_threads_resume.add_argument("--thread-id", dest="thread_id", required=True)
    p_threads_resume.set_defaults(func=cmd_threads_resume)

    p_threads_abort = threads_sub.add_parser(
        "abort-replies", help="Founder: abort all pending reply invocations for a thread",
    )
    p_threads_abort.add_argument("--org", default=None, help="Org slug")
    p_threads_abort.add_argument("--thread-id", dest="thread_id", required=True)
    p_threads_abort.set_defaults(func=cmd_threads_abort_replies)

    p_threads_forward = threads_sub.add_parser("forward", help="Founder: forward a thread into a new thread")
    p_threads_forward.add_argument("--org", default=None, help="Org slug")
    p_threads_forward.add_argument("--source", required=True, help="THR-NNN")
    p_threads_forward.add_argument("--recipients", required=True, help="comma-separated agent names")
    p_threads_forward.add_argument("--note-file", dest="note_file", default=None)
    p_threads_forward.add_argument("--subject", default=None)
    p_threads_forward.set_defaults(func=cmd_threads_forward)
