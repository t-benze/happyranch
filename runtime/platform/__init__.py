"""Platform isolation primitives for macOS canonical store.

Provides macOS-only implementation for:
- Symlink creation and validation
- Executor process launch

Linux and Windows are NOT supported in this release — explicitly fail closed.
"""

from __future__ import annotations

from runtime.platform.isolation import (
    PlatformIsolation,
    detect_platform_isolation,
)

__all__ = [
    "PlatformIsolation",
    "detect_platform_isolation",
]
