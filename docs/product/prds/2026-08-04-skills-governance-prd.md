# Skills catalog and governance PRD — superseded

| Field | Value |
| --- | --- |
| Status | Superseded by THR-055 founder-directed retirement (seq 317, TASK-4749) |
| Original date | 2026-08-04 |
| Current implementation contract | `protocol/skills/create-skill/SKILL.md`, `runtime/daemon/routes/skills.py`, and generated OpenAPI |

This document is historical context only. Its proposal-review, Founder authoring,
validation, assignment, and eligibility workflow is deliberately retired and is
not a delivery plan.

## Current supported model

The read-only Skills catalog preserves status, source, and provenance. Direct
human create, validate, edit, and assignment endpoints are explicit `410
legacy_cutover`; proposal routes, UI, and CLI are absent; and `/skills/new` and
`/skills/:skillId/edit` render Not Found.

The sole creation path is B1 verified-agent creation through `create-skill` and
`POST /skills/agent`. Its server-derived task/session/agent provenance,
authorization boundary, and default-hidden status are unchanged. It does not
provide a human editor, proposal lifecycle, or eligibility remediation path,
and it never grants permissions.
