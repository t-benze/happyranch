# Operational evidence, observability, and assistant PRD

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | Product Lead |
| Date | 2026-08-04 |
| Source Links | THR-140 seq. 17–19; THR-145 seq. 2–3; current evidence/assistant web and daemon contracts |
| Commitment Boundary | analysis-only — reconciles present evidence flows; no assistant authority or retention commitment |
| Founder Decisions | Required: whether to authorize a separately designed, auditable assistant governance-write scope. Ruled: current dock is read-oriented and founder-authenticated canonical surfaces decide. |

## Problem and outcome

The founder needs credible records of work, usage, runtime health, learning candidates, and assistant tool evidence to investigate and route action. The outcome is a source-linked, caveated evidence layer that sends consequential action to the canonical record—not dashboards that fabricate certainty or an assistant that silently approves.

## Users and authority baseline

Founder-authenticated browser access and server checks govern artifacts, KB, dreams, audit, and assistant conversations. Artifacts require bearer authorization; thread attachments also require participant access. Browser UI attributes artifact writes as `founder`, but does not create a multi-user permission model. Assistant tools remain bounded by selected executor/runtime policy; conversation UI is not direct governance authority.

## Shipped constraints

- Dashboard/usage/health/metrics and daemon filesystem/process/config claims are snapshots that can be stale/unavailable. SSE/WebSocket improves timeliness but does not guarantee durable delivery.
- Artifacts retain provenance/metadata and stored access; diffs, checks, and review history are not facts unless separately persisted. Audit is append-only.
- Usage separates fresh input/output/reasoning from cache activity and has no authorized currency/cost meter.
- Dreams/reflection, private learning, candidate review, and KB promotion are separate records/transitions. KB create/update/delete/reindex follow their current browser routes; CLI-only `--as-founder` impersonation deletion is outside web scope.
- Assistant dock supports status/conversation management, transparent streaming/tool evidence, and navigation/handoffs. There is no canonical browser approval/denial write path.

## Scope and non-goals

In scope: evidence/dashboard projections, KB, artifacts/attachments, audit, usage, dreams/candidates, health/prereqs, global assistant dock/conversation switcher, and their truthful states.

Non-goals: currency billing, SLA claims, artifact diff/check/review history without records, retention-policy changes, CLI impersonation controls, new analytics metrics, direct assistant approvals, in-dock governance actions, and remote/multi-user RBAC.

## Functional requirements

1. **FR-1–4 Evidence:** dashboard/summary, KB records/search/stats, and all counters/statuses identify their source or are marked derived/unavailable/stale.
2. **FR-5–7 Artifacts:** list/tree/metadata/download/upload/delete and participant-scoped thread attachments expose retained provenance and authoritative external/repository linkage when inspection data is absent.
3. **FR-8–9 Audit:** provide filterable/paginated append-only timeline and create linked corrective events rather than rewriting historical meaning.
4. **FR-10–12 Usage:** show defined token categories and cache activity separately, preserve metric window/source, and never imply currency/cost absent a metering record.
5. **FR-13–15 Dreams/KB:** show dream/reflection/candidate origin, status, proposer/decision evidence where retained; accept/dismiss only through allowed review transition; keep private learning distinct from shared KB.
6. **FR-16–18 Health:** render health/prerequisite/metrics as time-bounded daemon evidence with failure/unavailable state and no availability promise.
7. **FR-19–23 Assistant:** show conversation identity, streamed answer/tool/source evidence, selected executor posture and navigation chips; route any task/job/thread/config action to its founder-authenticated canonical surface; never present tool activity or source citation as approval.

## Workflow and state behavior

Founder opens evidence view → reads source/freshness/caveat → drills to retained artifact/audit/KB/dream/task/thread/job record → completes any authorized action there. Dream candidate transitions are review decisions, not automatic KB promotion. Assistant conversation → tool/source evidence → navigation handoff; requests needing governance write remain outside the dock. WebSocket/SSE disconnect, stale, empty, denied, and failed states must remain visible and recoverable.

## API and data dependencies

Dashboard summary; KB list/search/detail/stats/write/reindex; artifact and attachment endpoints; audit query; token/metrics/history; dreams/status/candidate actions; health/prereqs; assistant status/conversations/WebSocket; and canonical task/job/thread/settings action routes. Required provenance includes source record ID, time/window, actor/action when retained, org/thread association, and executor/tool identity where available.

## UX and accessibility criteria

Label freshness, source, unavailable/derived values, and state changes in text as well as color. Use semantic tables/timelines with filters, keyboard-operable drawers/dialogs/conversation controls, accessible streamed-update announcements that do not steal focus, downloadable artifact names/types, and clear 401/403/retry guidance. Never make a citation chip look like an executed approval.

## Acceptance criteria

- Representative dashboard/usage/health values trace to source/window or visibly state derived/unavailable/stale.
- Artifact/attachment access honors bearer/participant contract and does not claim unavailable checks/diffs/history.
- Audit correction creates a new linked event; candidate accept/dismiss does not silently convert private learning into KB.
- Usage does not combine cache activity into a currency or undefined total.
- Assistant shows tool/source evidence and routes consequential action to a canonical surface; no in-dock approve/deny mutation exists.
- Keyboard and screen-reader flows cover loading, empty, error, stale/disconnected, populated, and authorization-denied states.

## Metrics

Measure source-labeled versus unavailable projections, stale/disconnect incidence, artifact/attachment access failures, audit query latency/error, candidate review outcomes, usage metric completeness, assistant handoff completion, and tool-evidence rendering failures. Do not use these as an SLA or cost meter.

## Risks and gates

Risk: snapshot/stream data looks authoritative or current when it is neither. Mitigate with provenance/freshness and canonical-record links. Risk: assistant presentation is mistaken for authority.

**Founder gate:** decide whether to authorize a bounded, auditable assistant governance-write scope; until then retain read-oriented handoff. **Engineering gate:** schema/route design for any direct assistant mutation, retention/metric definition, artifact inspection history, or missing provenance. These gates must close before extension build planning.
