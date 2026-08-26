"""Platform operations for the canonical skill store.

Provides explicit macOS and Linux same-owner implementations for:
- Symlink creation and validation
- Executor process launch

Windows and unknown platforms are NOT supported — explicitly fail closed.
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
