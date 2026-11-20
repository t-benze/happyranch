"""Daemon API compatibility numbers (shared by public health metadata)."""
from __future__ import annotations

# Only breaking API changes raise current; additive fields/routes do not.
# Raise min_client only to cut off old clients.
CURRENT = 1
MIN_CLIENT = 1
