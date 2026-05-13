"""Thread endpoints — email-style multi-agent workchannel."""
from __future__ import annotations

from fastapi import APIRouter

from src.daemon.auth import require_token

router = APIRouter(dependencies=[require_token()])
