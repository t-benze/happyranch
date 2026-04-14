"""HTTP client used by CLI commands and agent callbacks."""
from __future__ import annotations

from typing import Iterator

import httpx

from src.daemon import paths


class DaemonNotRunning(RuntimeError):
    """Raised when ~/.opc/daemon.port is missing."""


class DaemonStateInconsistent(RuntimeError):
    """Raised when the port file exists but the token file does not."""


class OpcClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"}
        self._client = httpx.Client(base_url=base_url, headers=self.headers, timeout=30.0)

    @classmethod
    def from_env(cls) -> "OpcClient":
        port_path = paths.port_file()
        if not port_path.exists():
            raise DaemonNotRunning(
                "daemon not running — start it with scripts/daemon.sh start"
            )
        port = port_path.read_text().strip()
        token = paths.read_token()
        if token is None:
            raise DaemonStateInconsistent(
                "daemon state inconsistent — restart via scripts/daemon.sh"
            )
        return cls(base_url=f"http://127.0.0.1:{port}", token=token)

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._client.get(path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._client.post(path, **kwargs)

    def stream(self, method: str, path: str, **kwargs) -> Iterator[str]:
        """Yield server-sent event payload lines (data: ... only)."""
        with self._client.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    yield line.removeprefix("data: ")

    def close(self) -> None:
        self._client.close()
