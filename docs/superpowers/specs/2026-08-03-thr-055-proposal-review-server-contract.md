# THR-055 Skill Proposal Review — retired historical record

**Status:** Retired by founder direction (THR-055 seq 317, TASK-4749).

This document formerly described a Founder proposal queue, proposal detail
pages, review actions, and lifecycle API. None is current behavior or a delivery
plan. The queue/detail UI, navigation, client API, proposal routes, CLI command,
and proposal-specific tests were removed as part of the retirement.

## Current contract

- Proposal endpoints under `/skill-lifecycle/proposals/*` are unregistered and
  historic web deep links render Not Found.
- Direct human `POST /skills`, `POST /skills/{skill_id}/validate`,
  `PATCH /skills/{skill_id}`, and
  `POST /agents/{agent_id}/skills/{skill_id}/assign` remain explicit `410`
  `legacy_cutover` retirement endpoints and are represented in generated
  OpenAPI.
- The supported B1 `happyranch skills create` / `POST /skills/agent` path is
  separate: it requires a verified active agent task/session, derives provenance
  server-side, accepts only `standard_operational` content, and creates a
  default-hidden record.

This retirement preserves the B1 provenance and authorization boundary. It does
not create a human editor, eligibility workflow, direct-authoring replacement,
or future proposal-review commitment.
