"""Pytest configuration for labs capacity harness unit tests.

The harness lives under ``labs/managed-remote-access/capacity/harness``
(non-package path because of the hyphenated lab directory). These unit
tests import its modules directly, so the harness directory is placed on
``sys.path`` here. Tests are pure-logic units: they never invoke docker,
tailscale, headscale, or any external process.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HARNESS_DIR = _REPO_ROOT / "labs" / "managed-remote-access" / "capacity" / "harness"

if str(_HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(_HARNESS_DIR))
