"""Thin wrapper around the lark-oapi SDK for sending Feishu post messages.

Phase 1 only needs `send_post_message`. Phase 2's event listener uses the SDK
WS client directly via `FeishuEventListener`.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class FeishuSendError(RuntimeError):
    """Raised when im.message.create returns a non-success response."""

    def __init__(self, code: int | None, msg: str) -> None:
        super().__init__(f"feishu send failed: code={code} msg={msg}")
        self.code = code
        self.msg = msg


class _SdkClient(Protocol):
    """Subset of lark_oapi.Client used by FeishuClient (for test injection)."""

    @property
    def im(self): ...


def _build_post_content(title: str, body_lines: list[str]) -> str:
    """Build the JSON content envelope for msg_type=post (zh_cn locale)."""
    payload = {
        "zh_cn": {
            "title": title,
            "content": [
                [{"tag": "text", "text": line}] for line in body_lines
            ],
        }
    }
    return json.dumps(payload, ensure_ascii=False)


class FeishuClient:
    def __init__(self, *, sdk_client: _SdkClient) -> None:
        self._sdk = sdk_client

    def send_post_message(
        self,
        *,
        chat_id: str,
        title: str,
        body_lines: list[str],
    ) -> str:
        """Send a post-format message to the given chat. Returns message_id.

        Raises FeishuSendError on any non-success response.
        """
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("post")
                .content(_build_post_content(title, body_lines))
                .build()
            )
            .build()
        )
        resp = self._sdk.im.v1.message.create(req)
        if not resp.success():
            raise FeishuSendError(
                code=getattr(resp, "code", None),
                msg=getattr(resp, "msg", "") or "(no msg)",
            )
        return resp.data.message_id

    def send_thread_reply(
        self,
        *,
        parent_message_id: str,
        title: str,
        body_lines: list[str],
    ) -> str:
        """Reply (threaded) to an existing Feishu message. Returns message_id.

        Used to send the founder a parse-failure hint nested under the
        notification thread, so retries land back in the same thread.
        """
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        req = (
            ReplyMessageRequest.builder()
            .message_id(parent_message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("post")
                .content(_build_post_content(title, body_lines))
                .reply_in_thread(True)
                .build()
            )
            .build()
        )
        resp = self._sdk.im.v1.message.reply(req)
        if not resp.success():
            raise FeishuSendError(
                code=getattr(resp, "code", None),
                msg=getattr(resp, "msg", "") or "(no msg)",
            )
        return resp.data.message_id
