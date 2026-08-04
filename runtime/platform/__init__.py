"""Platform isolation primitives for macOS.

Provides macOS-only implementation for:
- Daemon identity checks
- Symlink creation and validation
- Executor process launching under daemon identity

Linux and Windows are NOT supported in this release — explicitly fail closed.
"""

from __future__ import annotations

from runtime.platform.isolation import (
    PlatformIdentity,
    PlatformIsolation,
    detect_platform_isolation,
)

__all__ = [
    "PlatformIdentity",
    "PlatformIsolation",
    "detect_platform_isolation",
]
