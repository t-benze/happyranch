"""Shared doctrine strings surfaced in route error envelopes.

Centralized so threads and talks return identical hint text and stay in sync
when the wording evolves.
"""
from __future__ import annotations

SELF_DISPATCH_HINT = (
    "Threads (and talks) only accept self-dispatch.\n\n"
    "For cross-agent work, either:\n"
    "  (a) self-dispatch a manager root and delegate internally via the\n"
    "      manager-decision loop (recommended for iterative phase work), or\n"
    "  (b) use `grassland threads compose --to <other-agent>` to address\n"
    "      the other agent (or their team's manager) as a thread message,\n"
    "      and let them drive their own work.\n\n"
    "Cross-team handoffs always route through compose, not dispatch."
)
