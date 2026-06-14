"""HTTP client used by CLI commands and agent callbacks."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import httpx

from runtime.runtime import port_file, read_token


class DaemonNotRunning(RuntimeError):
    """Raised when ~/.happyranch/daemon.port is missing."""


class DaemonStateInconsistent(RuntimeError):
    """Raised when the port file exists but the token file does not."""


class OpcClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        # X-HappyRanch-Surface is a descriptive source LABEL identifying the
        # agent CLI, not an auth signal — never gate authz on it. The web SPA
        # must NOT send it so daemon routes can count agent-CLI reads only.
        # See KB: kb-view-tracking-caller-signal.
        self.headers = {
            "Authorization": f"Bearer {token}",
            "X-HappyRanch-Surface": "cli",
        }
        self._client = httpx.Client(base_url=base_url, headers=self.headers, timeout=30.0)

    @classmethod
    def from_env(cls) -> "OpcClient":
        port_path = port_file()
        if not port_path.exists():
            raise DaemonNotRunning(
                "daemon not running — start it with scripts/daemon.sh start"
            )
        port = port_path.read_text().strip()
        token = read_token()
        if token is None:
            raise DaemonStateInconsistent(
                "daemon state inconsistent — restart via scripts/daemon.sh"
            )
        return cls(base_url=f"http://127.0.0.1:{port}", token=token)

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._client.get(path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._client.post(path, **kwargs)

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self._client.request(method, path, **kwargs)

    def list_tokens(
        self,
        slug: str,
        task_id: str | None = None,
        agent: str | None = None,
        since: str | None = None,
        limit: int | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        talk_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        """Return per-session token usage rows for an org.

        Calls ``GET /api/v1/orgs/{slug}/tokens``. Filters AND-compose; ``None``
        values are omitted from the query string. Raises on non-2xx.
        """
        params = {
            k: v
            for k, v in {
                "task_id": task_id,
                "agent": agent,
                "since": since,
                "limit": limit,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "thread_id": thread_id,
                "talk_id": talk_id,
                "purpose": purpose,
            }.items()
            if v is not None
        }
        r = self.get(f"/api/v1/orgs/{slug}/tokens", params=params)
        r.raise_for_status()
        return r.json()["rows"]

    def aggregate_tokens(
        self,
        slug: str,
        group_by: str,
        task_id: str | None = None,
        agent: str | None = None,
        since: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        talk_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        """Return a token-usage rollup grouped by a supported token scope.

        Calls ``GET /api/v1/orgs/{slug}/tokens?group_by=...``. Filters
        AND-compose; ``None`` values are omitted. Raises on non-2xx.
        """
        if group_by not in ("agent", "task", "scope", "thread", "talk", "purpose"):
            raise ValueError(
                "group_by must be 'agent', 'task', 'scope', 'thread', "
                f"'talk', or 'purpose', got: {group_by!r}"
            )
        params = {
            k: v
            for k, v in {
                "group_by": group_by,
                "task_id": task_id,
                "agent": agent,
                "since": since,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "thread_id": thread_id,
                "talk_id": talk_id,
                "purpose": purpose,
            }.items()
            if v is not None
        }
        r = self.get(f"/api/v1/orgs/{slug}/tokens", params=params)
        r.raise_for_status()
        return r.json()["rollup"]

    def put_artifact(
        self,
        *,
        slug: str,
        local_path: Path,
        name: str | None,
        agent: str,
    ) -> dict:
        """Upload a local file to the org's shared artifacts store.

        Calls ``POST /api/v1/orgs/{slug}/artifacts`` with multipart form data.
        Raises on non-2xx.
        """
        params: dict[str, str] = {"agent": agent}
        if name is not None:
            params["name"] = name
        with local_path.open("rb") as fh:
            files = {"file": (name or local_path.name, fh, "application/octet-stream")}
            r = self._client.post(
                f"/api/v1/orgs/{slug}/artifacts",
                files=files,
                params=params,
            )
        r.raise_for_status()
        return r.json()

    def list_artifacts(self, *, slug: str, prefix: str = "") -> dict:
        """Return the org's artifact listing.

        Calls ``GET /api/v1/orgs/{slug}/artifacts``. Raises on non-2xx.
        """
        params: dict[str, str] = {}
        if prefix:
            params["prefix"] = prefix
        r = self._client.get(f"/api/v1/orgs/{slug}/artifacts", params=params)
        r.raise_for_status()
        return r.json()

    def get_artifact(self, *, slug: str, name: str) -> bytes:
        """Download an artifact by name and return its raw bytes.

        Calls ``GET /api/v1/orgs/{slug}/artifacts/{name}``. Raises on non-2xx.
        """
        r = self._client.get(f"/api/v1/orgs/{slug}/artifacts/{name}")
        r.raise_for_status()
        return r.content

    def stream(self, method: str, path: str, **kwargs) -> Iterator[str]:
        """Yield server-sent event payload lines (data: ... only)."""
        with self._client.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data:"):
                    payload = line[5:]
                    yield payload[1:] if payload.startswith(" ") else payload

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OpcClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
