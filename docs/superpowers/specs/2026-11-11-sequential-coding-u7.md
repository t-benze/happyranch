# Sequential Coding U7: common transport and agent initiation

> Status: current design; U7a read projections delivered, U7b–U7d implementation remains.
> Current Source: docs/agent-guides/contracts/reusable-workflows.md

THR-336 / ROOT TASK-15165; TASK-15465, linked retry of pre-edit failed TASK-15462.
Plan rev3 TASK-14461 §U7, R-M2-1, R-M3-3 and R-M3-4 govern. Product authority:
PRD v1.0.4 Shared Artifact `product_lead-2026-11-11-workflow-mechanism-prd-v1.0.4.md`,
SHA256 `96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`,
Founder THR-336 msg131 ACCESS-1. Source delivery is not deployment or acceptance.

## U7a assumptions and scope, declared before edits

No schema/table/column/index/migration, dependency or permission change. This
unit adds only existing-Founder-bearer browser GETs, closed projection records,
TS mirrors and provider read hooks. The existing closed StartCommand remains
`workflow-sequential-start@1`; no second command family or write is introduced.
Task sessions have no access through these routes. They retain their original
addressed-input readers and later U7c transport. The managed connector's explicit
allowlist is unchanged: all six new routes are denied there by default. Native
agent-only audiences receive no entitlement. Existing native local browser bearer
access remains the same audience; no new identity, credential or fallback.

Catalog lists only stored `release/happyranch/coding` versions. It does not register
packaged resources on GET or put Draft → Review fixtures in the production catalog.
Exact-version lookup uses the stored definition ID, including a fixture's admitted
version where a run links it. Definition JSON is the original canonical UTF8 text,
with source/definition digests and ABI pins, never a normalized replacement.
Missing sequential storage returns an explicitly unavailable empty catalog/run
list; selected settings/detail reads refuse. No installation or readiness follows.

## Routes and disclosure

All routes are under `/api/v1/orgs/{slug}` and use existing bearer authentication.
Unknown/duplicate queries and nonempty GET bodies refuse. Responses are no-store.

| GET | Projection |
| --- | --- |
| `/workflows/sequential/definitions` | Stored Coding versions; explicit storage availability |
| `/workflows/sequential/definitions/{definition_id}` | Exact immutable version/pins/definition text |
| `/workflows/sequential/settings?definition_identity=…` | Latest saved generation/digest, repo-free defaults/limits/admission; no work allocation |
| `/workflows/sequential/runs` | Authenticated run state/counters, pinned version and real source/carrier/visit links |
| `/workflows/sequential/runs/{run_id}` | Same run, original request/bindings/limits, visits/outcomes/findings, allowed UTF8 output content |
| `/tasks/{task_id}/sequential-workflow` | Same run projection or null for an ordinary/retained task; no invented tasks |

Existing Founder bearer task-result visibility permits the returned authored
content. No task session can use browser routes to widen its recipient set.
Fresh current authority and the complete combined-history oracle precede run
content/link disclosure; current eligible active fixed principals and the saved
immutable repository subject are checked. Current configured origins must still
match the original subject. A denial returns no partial list, task link, request,
output or historical proof material. Unavailable/invalid history or authority
returns409; positive role/subject denial returns403. Native absent task404 precedes
ownership409, including malformed existing ownership. No raw origin, local path,
SID/handle, native proof record, credential or executable Final selection is exposed.

`sequential_views` owns records and projections. It follows the existing SELECT-only
profile/publisher/database read pattern, with no durable admission lease, an owned
BEGIN/rollback snapshot and no DML. One original five-second deadline covers fresh
capture, complete read, projection and closing authority/serialization. The complete
oracle retains its original D2 A/B/C/L allowances; the bounded projection has its
own finite allowance on that same deadline, never retries/reset after refusal.
R-M3-2 per-read limits, original run budgets and cancellation owners are unchanged.

The route module is registered by create_app. Every route has one TS mirror in
`workflowSequential.ts` and an included classification entry. R-M3-4 retains
OpenAPI/classification/TS review parity and an out-of-repo synthetic diagnostic;
Vitest/openapi-coverage.test.ts remain retired. The provider contract and real/mock
implementations expose only reads. Org/selection keys, abort signals, zero retry,
no persisted cache, selection-exit eviction and hiding data during refresh/error
prevent stale cached content from supplying current access. U8 still owns UI.

## Required remaining implementation, in order

**U7b — repo-free settings PUT CAS and original-operation lookup.** Define the
closed command/receipt compatible with existing storage readers. Freeze one
original operation key/body through uncertain transport, and read its original
receipt separately from a newer current generation. Save preserves admission
and allocates zero work. Allow/disable future admission is a separate supported
setup action; saving cannot enable it. No repository/format/base/scope selector
in setup. Preserve old repo-bearing settings/key recovery with its old reader.

**U7c — selected task-session preflight and Final transport.** Subject-only
repository/base/scope/SHA1 preflight; no four-role duty fields or access observer.
Reject browser/foreign/recovery sessions and derive native task/principal/SID
from actual current binding. Mount the U5b2b1 local adapter only in the genuine
selected live Final-manager session, with compatible U5b2b2 callback reader,
current authority, U6 cancellation and original native ownership. Decide and
justify guarded-repository-merge@1 success admission in that coherent unit.
Until then **Final success stays Held**, with existing rollback-then-business-Held.
No daemon GitHub/network I/O, second CLI OrgState or job/recovery Final executor.

**U7d — ordinary workflow_start completion consumer.** Agent-prepared unchanged
StartCommand is consumed only by its real ordinary source completion. Principal,
source task, SID and positive INTEGER result are server-derived. No human/browser
start endpoint. Keep public fresh-start attachment OFF until U9 atomically fences
old fresh starts; no flag shortcut or historical engine fallback. Existing original
source return and thread association remain; notifications are excluded.

## Preserved owners and evidence limits

Historical workflow_coding_* families/routes/readers and old start@1/@2 behavior
remain unchanged; U9 owns cutover. TASK-15250 original Pause generation,
TASK-15272/15275 receipt-write uncertainty, U6b2a sequential-only proof,
Option B ordinary/document UNSETTLED and R-M3-2 remain with their owners.
ACCESS-1 means daemon current authority only. Effective access is proven solely
by each selected agent's own live operation; failure is denied versus unverified
BLOCK → Held. Agents share one OS user and gh credential; no isolation or lasting
grant is claimed. U4a/U4b2 local clone preflight is unchanged.

Out-of-repo synthetic diagnostics exercise actual routes/readers/projections
with explicit authority/native scaffolding, including denied/foreign disclosure,
404-before409, zero GET DML and no start/finalize. They are not native/V3/V5/M4,
real-agent access, genuine merge or acceptance evidence. Exact-head local CI is
a durable job; hosted checks are observed once, with no wait. Independent code
review, scoped QA and manager guarded merge remain required. Python units and
Vitest RETIRED; integration SKIPPED THR-243seq42; E2E untriggered.

All prior failed/REQUEST_CHANGES/QA-FAIL histories and consumed allowances are
preserved. V3 ends2026-11-23T08:48:38Z; extension, live settings/admission/roles,
installation/migration/deploy/restart, genuine Coding scratch and effect recovery
remain Founder-reserved. PR1252 remains held. F-1=(i), Decision1=B (48KiB JSON),
Decision2=1, SB-1=(a). Merged != deployed.
