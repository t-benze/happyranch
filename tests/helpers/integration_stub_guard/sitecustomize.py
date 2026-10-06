"""Imported only by the isolated test parent and its Python descendants."""
from __future__ import annotations

import os

if __name__ == "sitecustomize" and "PYTEST_CURRENT_TEST" in os.environ and "HAPPYRANCH_TEST_PARENT_MANIFEST" not in os.environ:
    os.write(2, b"integration test parent identity missing\n")
    os._exit(86)

if __name__ == "sitecustomize" and "HAPPYRANCH_TEST_PARENT_MANIFEST" in os.environ:
    try:
        from guard import install
        install()
    except Exception:
        # sitecustomize normally logs and continues after exceptions. Refuse
        # before pytest, a daemon, or a callback can run without the fence.
        os.write(2, b"integration stub identity unavailable\n")
        os._exit(86)
