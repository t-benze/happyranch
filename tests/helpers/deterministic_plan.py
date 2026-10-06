"""Explicit test-authored plan bytes, never an implicit success fallback."""
from __future__ import annotations

import hashlib
from pathlib import Path


def approve_plan(path: Path) -> None:
    sidecar = Path(str(path) + ".sha256")
    sidecar.write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    sidecar.chmod(0o600)


class DeterministicPlan(Path):
    def write_text(self, *args, **kwargs):
        result = super().write_text(*args, **kwargs)
        self.chmod(0o700)
        approve_plan(self)
        return result
