"""Read/write helpers for the daemon's ``runtimes.yaml`` registry."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.daemon import paths
from src.runtime import RuntimeDir


@dataclass
class RegistryState:
    active: Path | None = None
    registered: list[Path] = field(default_factory=list)


def load() -> RegistryState:
    path = paths.runtimes_file()
    if not path.exists():
        return RegistryState()
    raw = yaml.safe_load(path.read_text()) or {}
    active = raw.get("active")
    registered = raw.get("registered") or []
    return RegistryState(
        active=Path(active).resolve() if active else None,
        registered=[Path(p).resolve() for p in registered],
    )


def _save(state: RegistryState) -> None:
    paths.ensure_daemon_home()
    payload = {
        "active": str(state.active) if state.active else None,
        "registered": [str(p) for p in state.registered],
    }
    paths.runtimes_file().write_text(yaml.dump(payload, default_flow_style=False))


def register(path: Path) -> None:
    """Add *path* to the registry and make it active.

    Raises ``ValueError`` if *path* is not a valid runtime directory.
    """
    resolved = Path(path).resolve()
    RuntimeDir.load(resolved)  # raises if marker missing
    state = load()
    if resolved not in state.registered:
        state.registered.append(resolved)
    state.active = resolved
    _save(state)


def activate(path: Path) -> None:
    """Set *path* as the active runtime.

    Raises ``ValueError`` if *path* isn't already registered.
    """
    resolved = Path(path).resolve()
    state = load()
    if resolved not in state.registered:
        raise ValueError(f"{resolved} is not in the registry; call register() first")
    state.active = resolved
    _save(state)
