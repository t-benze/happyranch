"""System assistant setup and status commands."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import os
import signal
import termios
import sys
import tty
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.exceptions import WebSocketException

from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient

_RESIZE_CONTROL_PREFIX = "__HAPPYRANCH_ASSISTANT_RESIZE__"


def _client() -> OpcClient:
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def _print_status(body: dict[str, Any]) -> None:
    print(f"state: {body['state']}")
    if body.get("selected_executor"):
        print(f"executor: {body['selected_executor']}")
    if body.get("workspace_path"):
        print(f"workspace: {body['workspace_path']}")
    if body.get("detail"):
        print(f"detail: {body['detail']}")


def cmd_assistant_status(args: argparse.Namespace) -> None:
    client = _client()
    r = client.get("/api/v1/assistant/status")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    _print_status(r.json())


def _probe_passed(result: dict[str, Any]) -> bool:
    return result.get("passed") is True


def _probe_failure_reason(result: dict[str, Any]) -> str | None:
    reason = result.get("detail") or result.get("reason") or result.get("error")
    if reason:
        return str(reason)
    status = result.get("status")
    return str(status) if status else None


def _choose_executor(results: list[dict[str, Any]]) -> str:
    passing = [r for r in results if _probe_passed(r)]
    if not passing:
        print("No PTY-capable executor passed the HappyRanch probe.")
        for result in results:
            print(f"- {result.get('executor')}: {_probe_failure_reason(result) or 'failed'}")
            if result.get("hint"):
                print(f"  hint: {result['hint']}")
        sys.exit(2)
    print("PTY-capable executors:")
    for idx, result in enumerate(passing, start=1):
        executor = str(result["executor"])
        print(f"{idx}. {executor} ({result.get('command', executor)})")
    while True:
        raw = input("Select executor: ").strip()
        try:
            selected = passing[int(raw) - 1]
        except (ValueError, IndexError):
            print(f"Enter a number from 1 to {len(passing)}.")
            continue
        return str(selected["executor"])


def cmd_assistant_init(args: argparse.Namespace) -> None:
    client = _client()
    status = client.get("/api/v1/assistant/status")
    if status.status_code != 200:
        print(f"Error ({status.status_code}): {status.text}")
        sys.exit(1)
    body = status.json()
    if body["state"] == "configured" and not args.reconfigure and not args.repair:
        _print_status(body)
        return
    if args.repair and not args.reconfigure:
        r = client.post("/api/v1/assistant/repair")
        if r.status_code != 200:
            print(f"Error ({r.status_code}): {r.text}")
            sys.exit(1)
        _print_status(r.json())
        return
    probes = client.post("/api/v1/assistant/probes")
    if probes.status_code != 200:
        print(f"Error ({probes.status_code}): {probes.text}")
        sys.exit(1)
    results = probes.json()["probe_results"]
    selected = _choose_executor(results)
    configured = client.post(
        "/api/v1/assistant/configure",
        json={"selected_executor": selected, "probe_results": results},
    )
    if configured.status_code != 200:
        print(f"Error ({configured.status_code}): {configured.text}")
        sys.exit(1)
    _print_status(configured.json())


def _ws_url(client: OpcClient) -> str:
    parsed = urlparse(client.base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/api/v1/assistant/session"


def _ws_headers(client: OpcClient) -> dict[str, str]:
    return {"Authorization": client.headers["Authorization"]}


def _connect_websocket(url: str, headers: dict[str, str]) -> Any:
    parameters = inspect.signature(websockets.connect).parameters
    header_kw = "additional_headers" if "additional_headers" in parameters else "extra_headers"
    try:
        return websockets.connect(url, **{header_kw: headers})
    except TypeError as exc:
        if header_kw == "additional_headers" and "additional_headers" in str(exc):
            return websockets.connect(url, extra_headers=headers)
        if header_kw == "extra_headers" and "extra_headers" in str(exc):
            return websockets.connect(url, additional_headers=headers)
        raise


def _resize_control_message(fd: int) -> str | None:
    try:
        size = os.get_terminal_size(fd)
    except OSError:
        return None
    if size.lines <= 0 or size.columns <= 0:
        return None
    return f"{_RESIZE_CONTROL_PREFIX} {size.lines} {size.columns}"


async def _attach_bridge(client: OpcClient) -> None:
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)
        async with _connect_websocket(_ws_url(client), _ws_headers(client)) as websocket:
            loop = asyncio.get_running_loop()
            bridge_done = loop.create_future()
            stdin_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1024)
            backpressure_tasks: set[asyncio.Task[Any]] = set()
            reader_active = False
            resize_handler_registered = False

            def remove_stdin_reader() -> None:
                nonlocal reader_active
                if reader_active:
                    loop.remove_reader(fd)
                    reader_active = False

            def fail_bridge(exc: Exception) -> None:
                remove_stdin_reader()
                if not bridge_done.done():
                    bridge_done.set_exception(exc)

            def add_stdin_reader() -> None:
                nonlocal reader_active
                if not reader_active and not bridge_done.done():
                    loop.add_reader(fd, send_stdin_char)
                    reader_active = True

            async def enqueue_after_space(item: str | None) -> None:
                try:
                    await stdin_queue.put(item)
                except Exception as exc:
                    fail_bridge(exc)
                    return
                if item is not None:
                    add_stdin_reader()

            def queue_stdin_item(item: str | None) -> None:
                try:
                    stdin_queue.put_nowait(item)
                except asyncio.QueueFull:
                    remove_stdin_reader()
                    task = asyncio.create_task(enqueue_after_space(item))
                    backpressure_tasks.add(task)
                    task.add_done_callback(backpressure_tasks.discard)

            def send_stdin_char() -> None:
                if not reader_active or bridge_done.done():
                    return
                try:
                    data = sys.stdin.read(1)
                except Exception as exc:
                    fail_bridge(exc)
                    return
                if not data:
                    remove_stdin_reader()
                    queue_stdin_item(None)
                    return
                queue_stdin_item(data)

            def queue_resize() -> None:
                message = _resize_control_message(fd)
                if message is not None:
                    queue_stdin_item(message)

            def add_resize_handler() -> None:
                nonlocal resize_handler_registered
                add_signal_handler = getattr(loop, "add_signal_handler", None)
                if add_signal_handler is None:
                    return
                try:
                    add_signal_handler(signal.SIGWINCH, queue_resize)
                except (NotImplementedError, RuntimeError, ValueError):
                    return
                resize_handler_registered = True

            def remove_resize_handler() -> None:
                if not resize_handler_registered:
                    return
                remove_signal_handler = getattr(loop, "remove_signal_handler", None)
                if remove_signal_handler is None:
                    return
                with contextlib.suppress(RuntimeError, ValueError):
                    remove_signal_handler(signal.SIGWINCH)

            async def send_stdin_to_websocket() -> None:
                try:
                    while True:
                        data = await stdin_queue.get()
                        if data is None:
                            return
                        await websocket.send(data)
                except Exception as exc:
                    fail_bridge(exc)
                    raise

            queue_resize()
            add_resize_handler()
            add_stdin_reader()
            try:
                receive_task = asyncio.create_task(_write_websocket_output(websocket))
                sender_task = asyncio.create_task(send_stdin_to_websocket())
                finished, pending = await asyncio.wait(
                    {receive_task, sender_task, bridge_done},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                error: BaseException | None = None
                for task in finished:
                    try:
                        task.result()
                    except Exception as exc:
                        error = exc
                if bridge_done.done():
                    try:
                        bridge_done.result()
                    except Exception as exc:
                        error = exc
                for task in pending:
                    task.cancel()
                queued_backpressure_tasks = list(backpressure_tasks)
                for task in queued_backpressure_tasks:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.gather(
                    *queued_backpressure_tasks,
                    return_exceptions=True,
                )
                await websocket.close()
                if error is not None:
                    raise error
            finally:
                remove_stdin_reader()
                remove_resize_handler()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


async def _write_websocket_output(websocket: Any) -> None:
    async for message in websocket:
        if isinstance(message, bytes):
            sys.stdout.buffer.write(message)
            sys.stdout.buffer.flush()
        else:
            sys.stdout.write(message)
            sys.stdout.flush()


def _run_attach_bridge(client: OpcClient) -> None:
    asyncio.run(_attach_bridge(client))


def cmd_assistant_attach(args: argparse.Namespace) -> None:
    client = _client()
    status = client.get("/api/v1/assistant/status")
    if status.status_code != 200:
        print(f"Error ({status.status_code}): {status.text}")
        sys.exit(1)
    state = status.json()["state"]
    if state == "uninitialized":
        print("System assistant is not initialized. Run `happyranch assistant init`.")
        sys.exit(2)
    if state != "configured":
        print(
            "System assistant configuration needs repair or reconfigure. "
            "Run `happyranch assistant init --repair` or "
            "`happyranch assistant init --reconfigure`."
        )
        sys.exit(2)
    try:
        _run_attach_bridge(client)
    except (OSError, WebSocketException) as exc:
        print(f"Error: assistant attach failed: {exc}")
        sys.exit(1)



def register(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = sub.add_parser("assistant", help="manage or attach to the system assistant")
    p.set_defaults(assistant_cmd="attach", func=cmd_assistant_attach)
    assistant_sub = p.add_subparsers(dest="assistant_cmd")
    assistant_sub.default = "attach"
    assistant_sub.required = False

    p_init = assistant_sub.add_parser("init", help="initialize the system assistant")
    group = p_init.add_mutually_exclusive_group()
    group.add_argument("--repair", action="store_true")
    group.add_argument("--reconfigure", action="store_true")
    p_init.set_defaults(func=cmd_assistant_init)

    p_status = assistant_sub.add_parser("status", help="show system assistant status")
    p_status.set_defaults(func=cmd_assistant_status)

    p_attach = assistant_sub.add_parser("attach", help="attach to the system assistant")
    p_attach.set_defaults(func=cmd_assistant_attach)
