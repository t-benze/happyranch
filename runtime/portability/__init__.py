"""Organization-portability policy (THR-187).

Slice A only: an exhaustive fail-closed direct-org-root classifier, a pure
quiescence/zombie-detection eligibility check, and a founder-only audited
reconciliation surface. No archive, export, import, staging, transfer fence,
cancellation, or other transfer side effect is implemented here.

The pure modules (`roots`, `eligibility`) hold no daemon/DB state and are the
testable seam; `runtime/daemon/routes/portability.py` wires them to live
`OrgState`/`DaemonState`.
"""
