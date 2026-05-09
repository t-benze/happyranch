"""Tiny FastAPI app that mimics enough of the Feishu Open Platform to test
our outbound flow. Specifically:
- POST /open-apis/auth/v3/tenant_access_token/internal
- POST /open-apis/im/v1/messages?receive_id_type=...
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request


def make_fake_feishu() -> tuple[FastAPI, dict[str, Any]]:
    state: dict[str, Any] = {
        "token_calls": 0,
        "messages": [],
    }
    app = FastAPI()

    @app.post("/open-apis/auth/v3/tenant_access_token/internal")
    async def issue_token():
        state["token_calls"] += 1
        return {
            "code": 0,
            "msg": "ok",
            "tenant_access_token": f"tat-{state['token_calls']}",
            "expire": 7200,
        }

    @app.post("/open-apis/im/v1/messages")
    async def create_message(request: Request):
        receive_id_type = request.query_params.get("receive_id_type", "")
        body = await request.json()
        msg_id = f"om_{len(state['messages']) + 1}"
        state["messages"].append({
            "receive_id_type": receive_id_type,
            "body": body,
            "message_id": msg_id,
        })
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "message_id": msg_id,
                "chat_id": body.get("receive_id"),
                "msg_type": body.get("msg_type"),
            },
        }

    return app, state
