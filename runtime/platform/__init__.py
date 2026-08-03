"""Platform isolation primitives for cross-platform canonical store ownership.

Provides Unix (Linux/macOS) and Windows implementations for:
- Service/daemon identity checks
- Restricted executor identity provisioning
- Filesystem ownership, ACL, and permission enforcement
- Symlink/junction creation and validation
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
