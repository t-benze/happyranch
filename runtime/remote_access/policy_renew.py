"""Fixed zero-input automatic policy renewal; no lifecycle or key actions."""
from __future__ import annotations

import os
import sys

from runtime.remote_access.installer import ManagedInstaller


def main() -> int:
    if len(sys.argv) != 1:
        print("invalid-request", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("root-required", file=sys.stderr)
        return 1
    try:
        ManagedInstaller(layout="deb").renew_policy()
    except Exception:
        # Neither validation input values nor underlying custody paths escape.
        print("renewal-refused", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
