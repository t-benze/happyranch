"""Unit tests for FeishuClient.

Mocks the lark-oapi SDK Client object — we never make real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.infrastructure.feishu.client import FeishuClient, FeishuSendError


def _ok_response(message_id: str = "om_test") -> MagicMock:
    resp = MagicMock()
    resp.success.return_value = True
    resp.data = MagicMock()
    resp.data.message_id = message_id
    return resp


def _err_response(code: int = 99991663, msg: str = "boom") -> MagicMock:
    resp = MagicMock()
    resp.success.return_value = False
    resp.code = code
    resp.msg = msg
    resp.data = None
    return resp


def test_send_post_message_calls_create_with_post_payload():
    sdk = MagicMock()
    sdk.im.v1.message.create.return_value = _ok_response("om_123")

    client = FeishuClient(sdk_client=sdk)
    msg_id = client.send_post_message(
        chat_id="oc_x", title="Subject Here",
        body_lines=["line one", "line two"],
    )
    assert msg_id == "om_123"

    args, kwargs = sdk.im.v1.message.create.call_args
    req = args[0]
    # Receive id type and recipient
    # The lark-oapi Request builders set params and body as attributes; our
    # FeishuClient builds the request via their builder API. Inspect via:
    payload = json.loads(req.body.content)
    assert payload["zh_cn"]["title"] == "Subject Here"
    lines = payload["zh_cn"]["content"]
    assert lines == [
        [{"tag": "text", "text": "line one"}],
        [{"tag": "text", "text": "line two"}],
    ]
    assert req.body.receive_id == "oc_x"
    assert req.body.msg_type == "post"


def test_send_post_message_raises_on_error_response():
    sdk = MagicMock()
    sdk.im.v1.message.create.return_value = _err_response(99991663, "permission denied")
    client = FeishuClient(sdk_client=sdk)
    with pytest.raises(FeishuSendError) as ei:
        client.send_post_message(chat_id="oc_x", title="t", body_lines=["b"])
    assert ei.value.code == 99991663
    assert "permission denied" in str(ei.value)
