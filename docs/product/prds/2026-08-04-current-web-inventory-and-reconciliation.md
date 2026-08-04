# Current web inventory and PRD reconciliation

| Field | Value |
| --- | --- |
| Status | shipped |
| Owner | Product Lead |
| Date | 2026-08-04 |
| Source Links | THR-140 (EM review seq. 18, Frontend review seq. 17/19); THR-145 (EM and Frontend route/contract inventory); TASK-4325; current `web/src/routes.tsx` and `web/src/lib/api/` |
| Commitment Boundary | analysis-only — this traceability record neither authorizes a build nor changes a shipped contract |
| Founder Decisions | Ruled: use founder-authenticated browser actions as the current baseline. Required: any new assistant governance-write authority only. |

## Purpose and reconciliation rule

This is the audited current-web inventory retained from PR #555. It replaces no product decision: it proves that every route-mounted shipped family is either tied to a requirement in one of the four domain PRDs or explicitly out of scope with an owner. “Shipped” means a present browser/API contract, not a new roadmap commitment or a claim that the prototype is authoritative.

The founder-supplied HTML bundle is evidence of proposed UX only. When it conflicts with implementation, the current daemon and web contract wins. In particular, Work Hours and the skills lifecycle are shipped constraints, not greenfield designs; prototype-only controls are not requirements.

## Shared authority, data, and UX baseline

- The SPA receives a localhost-gated daemon bearer from `GET /api/v1/auth/bootstrap`, stores it in `sessionStorage`, and retries one 401 once. Browser UI has no client-side role/RBAC gate; daemon routes enforce authorization and org/participant checks. Scoped registration tokens are exceptional flows, not general remote access.
- Founder-authenticated actions are the present authority baseline. Generalized operator/delegated roles, arbitrary remote-browser access, and an in-dock assistant approval surface are out of scope until separately designed and Founder-ruled.
- Dashboard, metrics, health, filesystem/process/config, executor, and SSE data are daemon snapshots/projections that may be unavailable or stale. SSE augments cache/polling; it is not a durable delivery guarantee. Every domain PRD requires clear unavailable/derived/stale treatment.
- AppShell, command palette, help drawer, keyboard shortcuts, theme/density, loading, empty, error, and 401/403 feedback are shared interaction infrastructure. They must preserve keyboard operation, visible focus, semantic controls/labels, non-color state cues, and readable error recovery; they are not independent product domains.

## Coverage crosswalk

| Current route or component family | Contract baseline | Requirement / explicit disposition |
| --- | --- | --- |
| `/`, `/onboarding`, org switcher/Add Organization | `GET/POST /orgs`, runtime/connect and scoped executor registration | Core operations PRD FR-1–4; org-less onboarding and executor/agent separation. |
| AppShell, Sidebar, AppBar, TopBar, command palette, help drawer, error boundary | shared org route and browser-auth infrastructure | Core operations PRD UX-1–5; visual primitives/layouts excluded as Frontend Engineering quality work. |
| Dashboard | `GET /orgs/{slug}/dashboard/summary` | Core FR-5 and Evidence FR-1; source-linked triage only. |
| Threads list/detail/inbox/SSE | list/detail/messages/tasks plus compose/send/invite/remove/extend/archive/resume/abort | Core FR-6–8; actions are canonical records, not role inference. |
| Tasks list/root/detail/recall/tail/attachments | create, cancel, revisit, resolve-escalation; linked jobs | Core FR-9–11; retain lineage and action provenance. |
| Todos (`/todos/:scheduleId`) | persisted schedule list/detail/pause/cancel/edit | Existing `2026-07-19-agent-todos.md` is the independently shippable product requirement; Core FR-12 covers console integration only. It remains distinct from Work Hours. |
| Agents/enrollment and team/executor/model/repo controls | list/create/init/enroll/approve/reject/memory/bindings | Core FR-13–15; no autonomy or new permission model implied. |
| Jobs list/detail/output/tail/wait | run/reject/stop and retained command context | Core FR-16–18; prototype Thread/routed-via/kind/PR fields are an Engineering data gap. |
| Settings: assistant/system/org/agents/executors | org/team writes; runtime/executor/profile/adapter contracts | Core FR-19–22. System values remain read-only when restart/route constraints say so. |
| Work Hours overview/detail/wakes and `/schedule` redirect | effective config/status/next wakes/settings/teams/agents | Work Hours PRD FR-1–10; redirect is compatibility only. |
| Skills catalog/detail/status and mounted legacy create/edit/validation/assignment routes | Catalog/status/validation reads remain compatibility evidence. Direct `POST /skills`, `PATCH /skills/{skill_id}`, direct validation, and direct assignment return `410 Gone` / `legacy_cutover`. `/skills/new` and `/skills/:skillId/edit` remain mounted, with legacy-named controls/adapters. | Skills PRD FR-1–5. Retired compatibility, not working acceptance: the mounted pages and legacy catalog flow are an Engineering implementation-reconciliation defect. This record does not authorize a remedy. |
| Skills lifecycle proposals queue/detail and lifecycle actions | The versioned proposal/decision ledger is the sole current Skills write boundary: verified-session proposal submission plus founder-gated lifecycle validation, review, publication, assignment, rollback, and retirement. | Skills FR-6–13. Immutable proposal/decision history remains separate from assignment and materialization projections; current proposal UI is bounded by the actions actually wired and server-authorized. |
| Knowledge Base | list/search/detail/stats, create/update/delete/reindex | Evidence PRD FR-1–4. CLI-only `--as-founder` impersonation path excluded (Engineering). |
| Artifacts and thread attachments | list/upload/download/delete, participation-scoped attachment download | Evidence FR-5–7; checks/diffs/review history need persisted records. |
| Audit | paginated/filterable append-only timeline | Evidence FR-8–9; corrections are new events. |
| Usage and `/spend` redirect | tokens/metrics/history | Evidence FR-10–12; no currency claim without meter. |
| Dreams and KB-candidate actions | status/list/detail, accept/dismiss candidate | Evidence FR-13–15; reflection, learning, and KB promotion remain distinct. |
| Health/prereqs | daemon-global health and prerequisite probes | Evidence FR-16–18; evidence, not SLA. |
| Assistant dock and conversation switcher | status/conversation CRUD, WebSocket turns, transparent tool activity | Evidence FR-19–23; not a governance-write surface. |
| `/__prototypes/*`, `/__design__`, mock providers, tests, scripts, static assets | build-gated development/design support | Excluded: frontend/design-system owner; not authoritative runtime product behavior. |
| NotFound and bookmarks/index redirects | navigation plumbing (`/spend`, `/schedule`, org index) | Excluded as independent scope; retain behavior only as compatibility. |

## Reconciliation completeness and retained decisions

Each page’s supporting dialogs, drawers, rows, state views, query/mutation hooks, and component-registry primitives inherit the table disposition; they are not silently omitted. The four independently shippable domains are intentionally separate: core operations; Work Hours; skills governance; evidence/observability/assistant.

Retained decisions: (1) Work Hours uses implemented leaf resolution, one org gate plus eligibility, atomic validation/last-known-good, and its wake-to-root-task contract; (2) Skills direct catalog writes are retired compatibility (`410 legacy_cutover`), while the lifecycle proposal ledger is the sole current write boundary; (3) Skills preserve immutable proposal/decision history separately from assignment/effectiveness and materialization projections, with terminal rejection; (4) browser authority remains founder-authenticated; and (5) assistant conversations and tool evidence do not imply direct approval authority.

## Genuine gaps and gates

- **Engineering gate:** add JobRecord routing/thread/kind/PR fields only through a schema/route proposal; do not render prototype sample data as facts.
- **Engineering + Founder gate:** any Work Hours holiday/blackout, urgent override, held escalation, calendar/timeline, print/export, or routine-editor extension needs a backed contract and separately approved scope.
- **Engineering implementation-reconciliation defect (no product build authorization):** `/skills/new`, `/skills/:skillId/edit`, and related legacy catalog controls remain mounted after their direct API contract was retired. Triage their bounded technical remedy separately; do not treat their presence as current product acceptance.
- **Founder + Engineering gate:** proposal participant/action/authority expansion, revival of direct catalog writes, delegated roles, or assistant governance writes require authority design. The sole currently requested Founder decision is whether to authorize a bounded, auditable assistant governance-write scope; absent that ruling, the read-oriented posture remains.
