"""Bearer-token auth dependencies for the daemon's FastAPI routes."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from runtime.daemon import paths
from runtime.daemon.registration_token import REGISTRATION_TOKEN_PREFIX

# Loopback hosts for registration-token-gated routes (defense-in-depth).
# Mirrors the _LOCAL_HOSTS pattern in routes/auth.py.
_REGISTRATION_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _check_token(authorization: str | None = Header(default=None)) -> None:
    expected = paths.read_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="daemon token file missing",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad token")


def require_token() -> Depends:
    return Depends(_check_token)


# ── Registration token dependency (PR-1 of THR-052) ────────────────────


def _check_registration_token(
    authorization: str | None = Header(default=None),
    request: Request = None,
) -> None:
    """Validate a scoped ``hrreg_`` registration token.

    Accepts ONLY the scoped ``hrreg_`` token — NOT the master bearer.
    Additionally loopback-gated (defense-in-depth: even a leaked scoped token
    is unusable off-host).

    The token store is accessed via ``request.app.state.daemon``.
    """
    # Loopback gate (defense-in-depth).
    peer = request.client.host if request.client else None
    if peer not in _REGISTRATION_LOCAL_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_localhost", "peer": peer},
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )

    token_value = authorization.removeprefix("Bearer ").strip()

    # Reject master bearer — this dependency is scoped-token-only.
    master = paths.read_token()
    if master is not None and token_value == master:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="master bearer not accepted on registration route",
        )

    # Reject tokens that don't carry the registration prefix.
    if not token_value.startswith(REGISTRATION_TOKEN_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not a registration token",
        )

    # Validate through the in-memory token store (org-agnostic — the
    # route consumer in PR-2 does org/name matching).
    store = request.app.state.daemon.registration_token_store
    record = store._validate_raw(token_value, now=None)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired registration token",
        )


def require_registration_token() -> Depends:
    """Auth dependency that accepts ONLY scoped ``hrreg_`` registration tokens.

    Rejects:
    - The master bearer
    - Non-hrreg_ tokens
    - Expired registration tokens
    - Consumed (already-used) registration tokens
    - Requests from non-loopback peers

    Mount this on ``POST /executors/register`` (PR-2). Do NOT mount it on any
    existing route — those keep ``require_token()`` which automatically rejects
    ``hrreg_`` tokens (they don't match the master bearer string).
    """
    return Depends(_check_registration_token)


def _check_optional_token(
    authorization: str | None = Header(default=None),
) -> bool:
    """Return True iff a valid bearer token is present. False otherwise.

    Used by dual-auth routes (bearer OR session-binding). Unlike
    ``_check_token``, this never raises — it lets the caller decide whether
    a missing/invalid bearer is acceptable when other proof of identity
    (e.g. session_id) is available.
    """
    expected = paths.read_token()
    if expected is None:
        # Daemon mis-configured. Fail closed for safety.
        return False
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return authorization.removeprefix("Bearer ").strip() == expected


def optional_bearer() -> Depends:
    return Depends(_check_optional_token)
