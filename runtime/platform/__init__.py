"""Platform isolation primitives for macOS canonical store ownership.

Provides macOS-only implementation for:
- Service/daemon identity checks
- Restricted executor identity provisioning
- Filesystem ownership and permission enforcement
- Symlink creation and validation

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
