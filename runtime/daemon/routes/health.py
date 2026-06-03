"""Liveness endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    state = request.app.state.daemon
    return {
        "status": "ok",
        "active_runtime": str(state.runtime.root) if state.runtime else None,
    }
