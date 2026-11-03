#!/usr/bin/env python3
"""Focused synthetic attachment GET regressions, outside pytest and daily E2E.

Run: uv run python scripts/verify_attachment_downloads.py
Uses the real routers and file stores with disposable metadata. Auth/org
dependencies are isolated in the test app; this does not verify bearer auth,
upload MIME admission, database persistence, or daemon lifecycle behavior.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI, HTTPException

from runtime.daemon.auth import _check_token
from runtime.daemon.routes import tasks, threads
from runtime.daemon.routes._org_dep import resolve_org
from runtime.infrastructure.task_attachment_store import (
    TaskAttachmentInvalidName,
    TaskAttachmentStore,
    sanitize_display_name,
)
from runtime.infrastructure.thread_scoped_attachment_store import ThreadScopedAttachmentStore
from runtime.orchestrator._paths import OrgPaths


# Literal independent wire expectations; never derive the oracle with the helper.
DOWNLOAD_CASES = (
    ("unicode-chinese", "报告-模板.docx", "__-__.docx",
     "%E6%8A%A5%E5%91%8A-%E6%A8%A1%E6%9D%BF.docx"),
    ("unicode-emoji", "📄-資料.docx", "_-__.docx",
     "%F0%9F%93%84-%E8%B3%87%E6%96%99.docx"),
    ("ascii", "report-template.docx", "report-template.docx", "report-template.docx"),
    ("sensitive", 'report "100%" + #?.docx', "report _100%_ + #?.docx",
     "report%20%22100%25%22%20%2B%20%23%3F.docx"),
)
SAFETY_CASES = (
    ("CR", "\r", "%0D"),
    ("LF", "\n", "%0A"),
    ("NUL", "\x00", "%00"),
    ("DEL", "\x7f", "%7F"),
    ("backslash", "\\", "%5C"),
)
CONTENT = b"\x00synthetic attachment\r\n\xff\x80"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class Metadata:
    """Only metadata is synthetic; route response construction is never mocked."""

    def __init__(self) -> None:
        self.record = SimpleNamespace(
            display_name="", content_type=None, storage_key="file-key", task_id="TASK-fixture",
        )

    def get_thread(self, thread_id: str) -> object:
        return SimpleNamespace(id=thread_id)

    def get_thread_scoped_attachment(self, thread_id: str, attachment_id: str) -> object:
        return self.record

    def get_task(self, task_id: str) -> object:
        return SimpleNamespace(id=task_id)

    def get_task_attachment(self, task_id: str, storage_key: str) -> object:
        return self.record


async def check_downloads(root: Path) -> int:
    db = Metadata()
    org = SimpleNamespace(root=root, db=db)
    paths = OrgPaths(root)
    thread_store = ThreadScopedAttachmentStore(paths.threads_dir)
    task_store = TaskAttachmentStore(paths.task_attachments_dir)
    thread_store.put("THR-fixture", "attachment-key", CONTENT)
    task_store.put("file-key", CONTENT)

    app = FastAPI()
    app.include_router(threads.router, prefix="/api/v1/orgs/{slug}")
    app.include_router(tasks.router, prefix="/api/v1/orgs/{slug}")
    app.dependency_overrides[resolve_org] = lambda: org
    app.dependency_overrides[_check_token] = lambda: None
    events: list[dict] = []

    async def observed_app(scope: dict, receive: object, send: object) -> None:
        async def observe(message: dict) -> None:
            if message["type"] == "http.response.body":
                events.append(message.copy())
            await send(message)
        await app(scope, receive, observe)

    failures = 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=observed_app, raise_app_exceptions=False),
        base_url="http://synthetic",
    ) as client:
        for label, name, fallback, encoded in DOWNLOAD_CASES:
            # These are the actual name validators used by the upload routes.
            # Existing metadata is seeded; no DOCX upload/MIME claim is made.
            threads._validate_display_name(name)
            require(sanitize_display_name(name) == name, "task name admission")
            for kind, path in (
                ("thread", "/threads/THR-fixture/attachments/attachment-key?agent=founder"),
                ("task", "/tasks/TASK-fixture/attachments/file-key"),
            ):
                # Cover an explicit media type and the existing absent-type fallback.
                for media_type in ("application/pdf", None):
                    db.record.display_name = name
                    db.record.content_type = media_type
                    events.clear()
                    response = await client.get("/api/v1/orgs/fixture" + path)
                    try:
                        require(response.status_code == 200, f"status={response.status_code}")
                        require(response.content == CONTENT, "exact attachment bytes")
                        require(response.headers["content-type"] == (
                            media_type or "application/octet-stream"
                        ), "original media type/fallback")
                        expected = f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'
                        header = response.headers["content-disposition"]
                        require(header == expected, f"header={header!r}; expected={expected!r}")
                        require(all(32 <= b < 127 for b in header.encode("ascii")), "ASCII-safe header")
                        require(unquote(header.split("filename*=UTF-8''", 1)[1]) == name, "original UTF-8 name")
                        if kind == "task":
                            require(len(events) == 2 and events[0].get("more_body") is True
                                    and events[0]["body"] == CONTENT
                                    and events[1]["body"] == b""
                                    and not events[1].get("more_body"), "task streaming ASGI frames")
                        else:
                            require(len(events) == 1 and events[0]["body"] == CONTENT
                                    and not events[0].get("more_body"), "thread buffered ASGI frame")
                        require(db.record.display_name == name, "stored display name unchanged")
                        require(thread_store.read("THR-fixture", "attachment-key") == CONTENT
                                and task_store.read("file-key") == CONTENT, "stored bytes unchanged")
                    except AssertionError as exc:
                        failures += 1
                        print(f"FAIL download/{kind}/{label}/{media_type}: {exc}")
                    else:
                        print(f"PASS download/{kind}/{label}/{media_type}")
    return failures


def check_safety() -> int:
    from runtime.daemon.content_disposition import attachment_content_disposition

    failures = 0
    for label, char, encoded in SAFETY_CASES:
        name = f"report{char}.docx"
        try:
            header = attachment_content_disposition(name)
            require(header == (
                f'attachment; filename="report_.docx"; filename*=UTF-8\'\'report{encoded}.docx'
            ), f"unsafe or incorrectly encoded header: {header!r}")
            require(all(32 <= b < 127 for b in header.encode("ascii")), "printable ASCII only")
            require(unquote(header.split("filename*=UTF-8''", 1)[1]) == name, "original preserved")
            # Observe admission separately from defensive formatting. DEL differs
            # between the existing validators; preserve that existing difference.
            if label == "DEL":
                threads._validate_display_name(name)
            else:
                try:
                    threads._validate_display_name(name)
                except HTTPException as exc:
                    require(exc.status_code == 422
                            and exc.detail["code"] == "invalid_attachment_display_name",
                            f"thread admission/{label}")
                else:
                    raise AssertionError(f"thread admitted {label}")
            try:
                sanitize_display_name(name)
            except TaskAttachmentInvalidName:
                pass
            else:
                raise AssertionError(f"task admitted {label}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL helper/{label}: {exc}")
        else:
            print(f"PASS helper/{label}")
    return failures


async def main() -> int:
    print(f"source={Path(__file__).resolve().parents[1]} python={sys.executable} {sys.version}")
    with tempfile.TemporaryDirectory(prefix="attachment-downloads-") as scratch:
        failures = await check_downloads(Path(scratch))
    failures += check_safety()
    print(f"attachment downloads: failures={failures}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
