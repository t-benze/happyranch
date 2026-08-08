# Custom skill creation and eligibility PRD — superseded

| Field | Value |
| --- | --- |
| Status | Superseded by THR-055 founder-directed retirement (seq 317, TASK-4749) |
| Original date | 2026-08-05 |
| Current implementation contract | `protocol/skills/create-skill/SKILL.md`, `runtime/daemon/routes/skills.py`, and the generated OpenAPI contract |

This document is retained only as historical product context. Its former human
direct-authoring, editing, validation, assignment, eligibility, and proposal
review requirements are retired and must not be implemented or presented as a
roadmap.

## Current supported model

The only custom-skill creation path is B1 verified-agent creation through the
injected `create-skill` system skill and `POST /skills/agent`. The server derives
org, task, session, and agent provenance from an active verified session. It
accepts only `standard_operational` content, applies deterministic validation,
and creates the record hidden by default.

Direct human creation/import, validation, edit, assignment, and all legacy
proposal-review surfaces are deliberately retired. The direct mutation endpoints
return explicit `410 legacy_cutover`; proposal endpoints are unregistered; the
web catalog is read-only and legacy deep links render Not Found. This retirement
does not alter authentication, permissions, eligibility semantics, schemas,
custom-adapter behavior, or the B1 authorization boundary.

Existing records may be listed read-only. No operator-facing remediation,
Founder editor, or eligibility configuration path is currently supported. A
future capability requires a separately authorized product and server contract;
it must not be inferred from this superseded PRD.
