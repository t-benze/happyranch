"""Organization-portability policy (THR-187).

Slice A: an exhaustive fail-closed direct-org-root classifier, a pure
quiescence/zombie-detection eligibility check, and a founder-only audited
reconciliation surface.

Slice B: a per-org transfer fence, a data-only versioned archive format, and
capture/verify helpers for the SQLite backup, allow-list capture, B2 custom-skill
cross-checks, and legacy-skill quarantine evidence. Export/inspection/import are
wired through ``runtime/daemon/routes/portability.py`` (CLI-private; no browser).

The pure modules (``roots``, ``eligibility``, ``fence``, ``archive``) hold no
daemon/DB state and are the testable seam; ``capture`` operates on paths and
short-lived ``sqlite3`` connections. Slice C (rebind/rearm) is not implemented.
"""
