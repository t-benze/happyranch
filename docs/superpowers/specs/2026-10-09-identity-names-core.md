# Identity names v1 — current core, API, routing, CLI, prompt and UI contract

Status: combined uncommitted/unpublished candidate for TASK-10343 / THR-293.
Core/API/routing, CLI/prompt and provider-aware UI/type source are present.
The maker handoff owns actual frontend command/runtime/source/exit evidence;
source integration alone establishes no daemon/DB/browser acceptance.
Founder seq14 accepts additive naming/schema/lifecycle; seq22 fixes one daemon
per organization. Accepted corrected design TASK10352 SHA256
4ec3585a806e250a73e41f1a6255c213a1991ae161f032730b1b7cdf16d100e1;
independent TASK10357 PASS DESIGN ONLY; EM step5 accepts exact A radius.
This document describes source behavior only. EM step6 authorizes the bounded
API/A11 continuation; EM step7 accepts B1–B6 and authorizes routing. EM step8
covers CLI/prompt, step9 covers UI/types and step10 covers current-main integration
and supported frontend corrections/checks. No whole-feature acceptance follows.

## Org ownership and admission

Only OrgState, after existing generic migrations, complete owned workflow
validation, teams/settings and original attachment validation, installs names.
Naming-only preflight is read-only and never substitutes for baseline admission.
The exact identity_name_schema/owners/claims v1 DDL has no explicit indexes.
All-absent naming is the sole installable input. DDL, canonical defaults, founder
and singleton marker commit atomically with BEGIN IMMEDIATE and individual
statement execution. Complete reopen compares SQL, table_xinfo, FK and complete
index metadata to independently pinned literal references and validates bounded
rows and their cross-row ownership. Partial/newer/foreign/extra/corrupt naming
refuses without repair. Generic Database/runtime-audit never acquire names.

Complete legacy references cover fresh/v0/v2/v2-organic × F/E/G × naming absent/v1,
including all three current generic cleanup indexes. Every non-null schema SQL
object participates; no object filter/whitelist or candidate-derived baseline.
Unknown whole-database objects keep the existing strict reference refusal.
Authority-v2 remains observed-only. Explicit workflow scripts preserve naming
rows, row identities, spelling and revisions; ready replays are read-only and
absent naming is never installed by migration. Logical preservation is separate
from SQLite physical page equality. Every naming-installed database requires a
compatible naming reader, even on F; flat runtime schema1 still refuses.

## Identity, editable names and transactions

Identity is (org,kind,canonical ID). Present active/pending/terminated IDs and
founder reserve the shared case-insensitive ASCII namespace. Workspaces/history
cannot create identities. Editable names fullmatch [A-Za-z0-9][A-Za-z0-9_-]{0,63},
without trimming, and preserve spelling. Existing canonical parser admission
remains unchanged. Owner-bound `_worker` defaults/claims/ID addresses are valid;
editable `_worker`, arbitrary underscore claims and foreign-owner exceptions
refuse. No longer-ID cold admission is inferred from a route regex.

Typed internal classification separates permanent ID, current name and former
ownership; former names do not forward. Pending/terminated names do not confer
eligibility. Absent permanent claims remain reserved, without a current recipient.
Internal rename uses the existing coroutine writer gate, team ownership, db_lock
and synchronized SQLite transaction. Positive exact revision CAS, collision
check, old/new permanent claims, label/revision and one identity_name_changed
founder-scope audit share the transaction. Identical spelling is a no-op; case-only
and own-former reclaim advance once; lost response requires readback and stale
retry refusal. Pure rename neither canonical-publishes nor rebinds sessions,
workspaces, authority, roles, permissions, queues or configuration.

## Lifecycle and readiness

Capture scans literal canonical directories twice without following links,
brackets byte/inode identity and deadline, parses shipping AgentDef, and requires
saved/in-memory teams and full naming closure to agree. Limits:4096 definitions,
1MiB each,32MiB file total,65536 owners and claims,32MiB projected data,2-second
scan deadline. Limits/incoherence yield naming-unavailable; no truncation/repair.

Existing async_writer_interval and consumer_writer use the same outer coroutine
gate. Naming does not acquire it twice or substitute an RLock. Profile-before-
publication and callback db_lock→binding_lease→synchronized DB order are retained.
Capture/scans/awaits stay outside short leases; pure rename takes no canonical
change. Cancellation drains the physical consumer write and original terminal
compensation before projection. Cold/dynamic load and startup compatibility
batch integrate through their existing callers and retain original attach guards.

New ID creation and rename require fresh available naming. Existing same-ID
reads/callbacks and lifecycle writes preserve original baseline guards, responses,
eligibility, selector/bootstrap and byte-owned compensation. After actual terminal
file/team outcome, projection advances lifecycle exactly once without resetting
chosen names. Default-only rejection releases the provisional claim but retains
revision metadata; chosen/former ownership survives absence and exact-ID reuse
restores spelling. Naming never runs operational cleanup or proves its success.
C17c versus C18a differ in actual cleanup rows/audit despite the same terminated
projection; C19e may be active/coherent while workspace rollback remains archived.

## Current-message routing and verification

Routing source now classifies current names and permanent IDs case-insensitively
to canonical agent IDs using the unchanged @token grammar (including quoted and
email-interior matches). Any recognized former token rejects the entire current
message/recipient input with409 former_name and current_name, before thread-ID
allocation, exchange closure, callback settlement/consumption, message/audit/queue
mutation or application attachment finalization. Composer, speaker, task/session
and invocation fields remain ID-only; labels confer no authority. Current names
of pending/terminated owners do not designate wake targets. Unknown text and
self/nonparticipant-only mentions retain ordinary fallback when naming is ready.
Unavailable naming preserves actual ID behavior; non-ID addresses refuse503
naming_unavailable. No read adapter reconciles or writes names.

Current-founder-only MESSAGE classification is ephemeral and separate from
empty agent mentions. It suppresses broadcast and frozen-cohort fallback for
that message, while full-recipient required watermarks still advance. Mixed
founder/eligible-agent mentions route the agents. Founder stays human inbox only,
never participant/executor. BOOTSTRAP/TASK_FOLLOWUP keep their isolated broadcast.
Already-due stale-exchange catch-up tokens remain returned and enqueued after
commit; they are not attributed to the new founder-only message. Restart reads
existing canonical mentions_json/ranges/tokens/exchange/deferral rows, without
reparsing historical names or discarding pending work. Frozen priority membership
comes from the opening message's persisted mentions; no member/receipt column
or table is added.

Filesystem namespace capture precedes the connection RLock; DB-only snapshot
revalidation precedes the transaction's first effect. Compose/send/reply and the
shared non-HTTP dream compose boundary revalidate actions. Multipart reads finish
before final admission and application allocation/store/write has no intervening
await. Framework-owned request parsing/spooling is not published attachment
storage. The existing one-daemon ordering applies; no nested coroutine gate,
extra namespace lock or filesystem-under-DB lookup is introduced.

THR293seq34/35 requires twelve readable E2E scenarios, not the historical233/297
case matrix. Four authored routing bodies contribute to5/6/7/9/12 and remain
NOT RUN; original manifests are preserved as historical proposed work. Python
units/collection are SKIPPED/SUSPENDED, never PASS and never a blocker. General
broken integration is SKIPPED THR243seq42. Focused naming E2E belongs later in a
supported disposable GitHub/Mac Linux guest, with finite commands/resources and
actual cleanup; no blanket unspecified execution release is required. This
combined candidate supplies CLI/prompt and UI/types, editors, labels, pickers and
locale parity. Supported frontend checks and any concrete corrections are
recorded in the maker handoff; mock-backed results cannot establish daemon/DB
E2E or browser screenshots. Final whole-candidate independent reviewer APPROVE,
focused behavioral QA PASS, applicable checks/hooks, normal publication and
guarded manager merge remain required.

## Bounded naming API contract

All four endpoints are under `/api/v1/orgs/{slug}`:

| Endpoint | Auth/action | Contract |
| --- | --- | --- |
| GET /identities | Existing bearer, read-only | Founder plus current active/pending/terminated canonical definitions; no absent/history-only enumeration. |
| POST /identities/resolve | Existing bearer, read-only | Strict closed body: addresses (1–128 raw names/IDs), context thread_recipient/task_owner/lookup, optional thread_id only for thread context. Ordered typed results, no reservation/authorization. |
| PUT /agents/{agent_id}/addressable-name | Existing operator/human bearer dependency | Canonical path agent ID only, pending/active/terminated real owners allowed; absent refuses. |
| PUT /founder/addressable-name | Same operator trust | Typed human owner founder, never task agent/team participant/executor. |

Rename body is exactly strict addressable_name and positive strict integer
expected_name_revision. Missing/null/malformed/coerced/extra values refuse422;
raw body/query task/session/agent/composer/speaker/thread/invocation or actor/
principal/org identity-key presence refuses403 BEFORE model decoding, even
null/partial/malformed. Descriptive headers are ignored for authority and cannot
override bound denial. No auth/helper/permission change; shared bearer possession
cannot cryptographically prove an unbound caller is human.

IdentityView separates canonical_id, kind, lifecycle, addressable_name,
name_revision, canonical_definition_revision and naming_status. Agent list and
enrollment summaries add addressable_name/name_revision/naming_status; existing
name/ID/keys/revision stay canonical. Naming-only metadata failure yields nulls
and explicit unavailable without hiding baseline IDs. Read adapters validate a
fresh bounded snapshot, never reconcile/install/write. Agent canonical-definition
revision is the exact-content SHA256; founder has null definition revision.

Resolver uses ASCII fold and exact smaller label syntax; actual canonical IDs
retain owner-bound defaults such as _worker. No wildcard underscore labels,
Unicode, spaces, brackets or trimming. Resolved and ineligible results retain
kind/lifecycle; former_name returns current name as diagnostic with eligible=false,
never forwarding. Unknown lookup is explicit unknown_identity, independently of
ordinary unknown @text message parsing, which remains unchanged. Invalid syntax
has invalid_identity_address; unavailable naming has naming_unavailable. Task
owners require active actual team-roster agent definitions. Thread recipients
require active definitions/workspace and optional open existing thread membership;
founder is only a human inbox recipient. Pending/terminated remain lookup-only
agent subjects. Later action boundaries revalidate current eligibility/authority.

Rename reuses the A transaction/gate, exact CAS/no-op/case-only/former reclaim and
atomic existing-shaped audit. Name conflict/stale revision is409, absent owner404,
naming/storage refusal503 with category-only diagnostics. Lost/ambiguous response
requires readback through GET /identities before another CAS. No canonical change,
namespace generation, request receipt, initial enrollment label or lifecycle
prohibition is introduced. CLI/prompt source is below; UI/TS source is authored in TASK10399, unverified; routing source is described above.

A11 retains its twelve historical manifest-domain assertion bodies. Four readable
routing scenario bodies in the same integrated source contribute to current
scenarios5/6/7/9/12 via shipping routes and real DB boundaries. Every body is
NOT RUN. Original core14/233 and integrated24/297 manifests remain unchanged as
historical proposed work, not collection evidence or mandatory missing selectors.
CLI/prompt assertions are authored inside5/8/9; UI and detailed branch permutations remain unexercised. Static
AST/hash/diff evidence proves authoring/syntax only. The completed candidate still
requires focused disposable E2E, independent full review/QA and applicable
nonunit checks/hooks/publication/guarded merge. No feature completion is claimed.


## CLI human names and fresh prompt context

TASK10385 continues preserved TASK10379 under EM TASK10343 step8 and Founder
THR293seq14/22. This combined candidate is uncommitted/unpublished and unverified.
`happyranch identities list [--json]` shows current Name · ID, lifecycle and
name_revision. `identities resolve <addresses...> [--context lookup|task_owner|thread_recipient]
[--thread-id <id>] [--json]` uses the actual read-only API. `identities rename <current-name-or-id>
--name <ASCII-name> --expected-name-revision <positive-int> [--json]` renames an
agent or founder; alternatively `--from-file <JSON>` supplies exactly
addressable_name/expected_name_revision. Local syntax/payload checks precede
HTTP; extra binding keys are rejected, never stripped. Names are not human
credentials: operator rename retains the existing shared-bearer limitation.
Name revision differs from canonical definition revision. Ambiguous failures
cause one current-ID readback with an uncertain outcome and nonzero exit,
never a blind retry or inferred success. Another attempt needs reviewed state
and a fresh deliberate CAS.

Root `run --owner`, operator init/approve/reject/set-model/set-executor targets,
thread compose/invite/forward recipients and human task/audit/token/Todo/dream/
work-hours filters accept current ASCII names or permanent IDs, then send IDs.
Action owners retain eligibility/authorization. Former explicit targets report
the current name without forwarding; unknown/ineligible destinations refuse.
Founder recipients retain literal `@founder` on transport and are never agent
participants/owners. Existing historical canonical-ID queries and naming-
unavailable ID actions stay available under their original owner checks.
Human-readable task/thread/enrollment/Todo/dream/wake output uses current
Name · ID where metadata exists; JSON keys and IDs and historical body/audit
payload bytes remain unchanged. Current label enrichment is not a historical
name snapshot.

CLI-managed send/reply, multipart and shared compose paths check recognized
former body tokens before their associated upload requests; recipient resolution
also precedes compose uploads. Forward checks the final quoted+note body. The
unchanged server parser matches quoted text and email interiors; ordinary unknown
@text stays intact and bodies are never rewritten. A preflight is a read, not a
reservation across independent HTTP requests: a rename between preflight and
upload/send can still cause the authoritative action to refuse after a separate
upload exists. No rollback, atomic-upload protocol or lifecycle journal is added.
Independent artifact/attach-upload objects remain independent and ID-attributed.

Current task wrappers (including leaves/self-only), ID-selected manager rosters,
full and resumed thread prompts, full eviction/non-resumable retry fallback,
resumed no-callback nudge, and dream/wake/schedule production prompt callers
receive fresh org metadata. A resumed turn refreshes names even when no old
roster/history is resent. Each builder captures a bounded validated read for
all its labels; task role and outer wrapper are separate constructions. Founder
is separate human context. Metadata failure renders honest ID-only context,
without reconciliation, writes, cached snapshots or an added launch gate.
Callback actors/composer/speaker, task/session/invocation/provider-resume IDs,
delegate/then/fanout targets, role checks and config/frontmatter remain ID-bound.

Scenario8 now authors real CLI-entrypoint → loopback HTTP → stored-ID assertions
and actual shipping prompt builders after rename. Former-with-attachment checks
are inside5; prompt/session/file/queued-ID continuity is inside9. These are
AUTHORED / NOT RUN, within the existing twelve scenarios; no expansion of the
historical233/297 inventory. Unit execution/collection remains SKIPPED/SUSPENDED;
broken general integration remains SKIPPED THR243seq42. The combined candidate
includes frontend editors/pickers/locales and TypeScript adapters; exact
source-specific supported frontend checks are recorded in the maker handoff.
Genuine OpenAPI schema comparison, browser proof and supported disposable
behavioral E2E remain required, followed by final whole-diff independent
review/QA and hooked publication/guarded merge. Frontend mocks and static syntax
do not establish daemon/DB acceptance; no deployment or whole-feature completion.

## Functional naming UI and API contract

The existing org surface has an independent human-founder name editor. Active
agent detail, pending enrollment and terminated identity surfaces expose agent
name editors, keeping immutable ID and definition settings separate. Editable
names use exact ASCII 1–64 grammar (first letter/digit, then letters/digits/_/-),
without trimming. PUT sends only `addressable_name` and positive
`expected_name_revision`. A stale conflict performs authoritative GET, retains
the draft and requires explicit resubmission. An ambiguous response reads back
and reports the observed name/revision or uncertainty, with no blind retry or
claim that failed transport rolled back a commit. Absent/unavailable metadata
cannot rename. A displayed human label provides no authentication proof.

Provider-aware naming hooks share one org-scoped identity list/query. Successful
rename/readback refreshes identity metadata, preserving canonical IDs, settings
and unrelated drafts. Current labels display Name · ID (ID alone when equal or
unavailable); historical IDs, links, keys, owner/filter/recipient values and
stored message text remain canonical/literal. Metadata follows existing focus/
reconnect behavior and bounded 30-second polling; availability is never a global
gate for established ID operations. Editor drafts are scoped by org/kind/ID and
survive locale changes and identity refetches.

Real thread compose/follow-up/invite/forward and task read-filter pickers search
current names and permanent IDs. Selected values stay IDs across renames. Founder
is a separate human option routed as established `@founder`, never an agent
participant or task owner. There is no task-create owner GUI in the present web
source; the existing task agent filter is the relevant selector. Prospective
invite resolve omits `thread_id` because that optional scope means existing
participants; the actual invite endpoint retains final action eligibility.
Recognized former body/recipient tokens stop UI-managed associated attachment
uploads when caught by separate preflight. Unknown body @text remains literal,
including the existing quoted/email token parser behavior. This read is not a
reservation: final server admission may reject after an intervening rename, and
already-completed uploads are not claimed rolled back. New en/zh-CN text uses
existing controls/tokens; 390×844/1440×900 light/dark behavior and screenshots are
unexecuted acceptance obligations, not pixel-fidelity evidence.

The four existing naming adapters mirror IdentityView/ResolveResponse/RenameBody.
The repository contract snapshot is a summarized route/parameter/status artifact,
not full JSON Schema. Its four entries were authored manually from source; no
pytest regeneration ran. Actual schema comparison, supported frontend lint/
typecheck/build/tests, browser evidence and independent final-candidate review/
behavioral QA remain required. The shipping-page MSW assertions in
`web/src/test/identity-names.test.tsx` add UI portions of scenarios2/3/4/5/8/10;
API assertions are `web/src/lib/api/identities.test.ts`; scenario9 also exercises
canonical skill-row attributes and stable row identity during label refresh.
These provider/component tests use frontend mocks. Source-specific commands,
red/keeper results and actual execution status belong in the maker handoff;
frontend results do not establish daemon/DB end-to-end behavior. Existing
twelve-scenario integration bodies/fixtures are preserved. Python units remain SKIPPED/SUSPENDED;
general integration remains SKIPPED THR243seq42. No publication, merge, deployment
or whole-feature completion is established by this source slice.

## Current finite naming verification source (TASK10429, THR293seq34)

`tests/helpers/identity_names/run.sh` is the literal sequential selection for
these same twelve business scenarios, including necessary supplements at different
shipping seams. It adds no node-count acceptance rule. Core OrgState/SQL and
mounted ASGI/CLI assertions remain lower-layer evidence; only `test_identity_names_e2e.py`
starts a real disposable daemon. Its naming-only fixture uses unchanged
`scripts/daemon.sh`, private registration/home and ephemeral loopback ports,
independent release SQL and explicit source-bound native stub plans. Native
Claude thread replies and the genuine Codex completion callback use the
unchanged integration-parent CLI binding; no model or provider credential is used.
Naming-owned passive Node spawn observation records actual CLI/browser native
children and checks closure; literal native plans record their own/stub PID birth
identities. These observations change no spawn argument/result or product outcome.
The finite entry calls unchanged `integration_parent.py` only with clean committed
source and explicit nodes, retaining its actual nested Python/uv verification.

| Scenario | Selected source and observable outcome | Meaningful intended regression / keeper mutation |
| --- | --- | --- |
| 1 | core `test_scenario1_existing_org_upgrade_and_underscore`: one v0/F org with active `_worker`, pending `waiting`, terminated `retired`, retained task; whole reference validation, all retained rows/files unchanged, defaults/revisions stable after two reopens. | Omit pending/terminated scanning, reject owner-bound underscore default, or rewrite assigned_agent on upgrade; lifecycle/default/full-row assertions must fail. |
| 2 | e2e `test_scenario2_browser_agent_rename_and_stale_readback`: actual Agent route PUT200 and persisted name/revision/audit; concurrent real rename causes409, GET readback preserves draft and does not report success or retry; real second-tab locale event preserves node/focus/selection. | Turn stale409 into saved state, reset draft on readback/locale, or omit CAS; browser response/draft/PUT-count and SQL audit assertions must fail. |
| 3 | e2e `test_scenario3_browser_founder_name_and_durable_human_inbox`: actual Organization settings PUT; both founder address/name resolve as human; real compose persists founder attribution, with no participant/invocation. | Treat chosen founder name as an agent/wake target or replace permanent founder attribution; typed resolve and SQL assertions must fail. |
| 4 | core `test_scenario4_folded_collision_and_second_org`: current/former/ID/founder collisions refuse with exact alpha/beta state equality; beta independently uses Sam. | Drop fold/current/former ID ownership checks or make claims global; exact snapshot/refusal and second-org success assertions must fail. |
| 5 | integrated existing `test_scenario5_former_before_callback_and_multipart_effects`: mixed former/current stops before message/callback/upload effects; shipping CLI refuses before upload; ordinary unknown remains ordinary. | Move classification after effect/callback settlement or store allocation; existing exact no-effects/current-name assertions are keepers. |
| 6 | existing integrated `test_scenario6_current_names_and_unknown_fallback` plus e2e `test_scenario6_live_current_name_callback_and_unknown_fallback`: actual native callback settles canonical maker, ordinary unknown freezes both recipients and both native replies; parser/broadcast baseline retained. | Persist label instead of ID, forward unknown as name, or omit callback/recipient; actual SQL consumed/reply sequence and bound native-launch/callback witness assertions must fail. |
| 7 | existing integrated `test_scenario7_founder_only_preserves_open_and_stale_catchup`: human event/no new wake, preexisting open/stale obligations remain and catch up. | Convert founder-only to broadcast, suppress already-due catch-up, or rewrite exchange cohort; existing event/token/range/wake assertions are keepers. |
| 8 | existing integrated `test_scenario8_cli_targets_and_fresh_prompt_context` plus e2e `test_scenario8_live_cli_picker_and_persisted_ids`: real CLI targets current Lead, actual Codex callback completes manager task; real tasks query and thread picker POST retain maker ID across rename, locale and aborted upload; persisted participants/body and 32 real captures. | Submit mutable label, clear selection/draft on upload failure/locale, compose after failed upload, or fabricate callback; request/SQL/witness assertions must fail. |
| 9 | existing integrated `test_scenarios9_12_queued_ids_survive_rename_and_reopen` plus e2e `test_scenario9_live_history_authority_reopen`: actual task/result/session-usage/static authority audit and workspace/history IDs remain after rename and daemon restart; existing queued token/recipient/settlement survives independent OrgState reopen. | Rewrite authority/session/history bindings, rebuild queued token by alias, or replace workspace IDs; exact durable rows/file/token comparisons must fail. |
| 10 | integrated `test_scenario10_bound_and_unauthorized_rename`: explicit operator/task-full/thread-full arguments reuse real bearer denial, both orgs equality and actual bound-key403. | Admit bound key or wrong token, drift audit/claim/revision on refusal; existing equality/status assertions are keepers. Shared bearer still cannot prove a human principal. |
| 11 | core `test_scenario11_one_daemon_async_contention`: explicitly selects same-owner/different-label and two-owner folded rename CAS, then manager-enroll rename-first/create-first; both contenders queue on actual async writer interval; exactly one lawful winner, clean loser/no residual reservation. | Remove async writer interval or collision/CAS revalidation; queued-waiter/winner/loser/audit/no-file assertions must fail. Thread RLock alone is not proof. |
| 12 | core `test_scenario12_interrupted_lifecycle_and_reservations`: finite cuts C08/C12/C17b/C17c hard-kill real owned child after observed persisted operation; fresh reopen reconciles or explicitly refuses naming writes without drift; chosen/default rejection, same-ID restoration and stale-ABA arguments retain exact ownership/revision assertions. Existing integrated9/12 retains pre-B queued obligations. | Admit incoherent C17b rename, release chosen/former claim, leak rejected default, restore wrong spelling/revision, or replay queued alias; actual cut/child closure/row/refusal assertions must fail. |

Test-authoring four answers (`custom:6e62e269-d7ad-4f5f-8af7-29d1b5df9e2a`):
(1) the observable contract for each changed assertion is the table's real
response/row/file/DOM/child outcome; (2) its credible intended regression is the
same row's mutation, which must reach that assertion rather than fail during
setup/import; (3) nearest adequate coverage is the retained core schema/lifecycle,
A11 operator and integrated routing/CLI nodes plus shipping MSW naming UI tests;
the new distinct risks are finite selection, actual async serialization,
running-daemon callbacks/persistence and browser request/draft/selection behavior;
(4) no production test seam is introduced: only naming-owned fixtures, independent
literal release references, existing shipping routes/CLI and native stub ABI,
real concurrent requests, supported second-tab storage events and browser-only
upload transport abort. No mock success/readback/DB response is substituted.
Existing assertions remain keepers, except obsolete whole-manifest completion
accounting is removed from per-case receipt emission. No business keeper is
deleted. Red/green, mutation and keeper execution are **NOT RUN** in this authoring
leg; static syntax is not causal proof. Their actual results remain required
execution/QA evidence, never inferred from the source.

The historical manifest and all unselected bodies stay preserved. In particular,
whole fresh/v2-organic/F/E/G/operator migration Cartesian loops, all input grammar
permutations, founder enrollment races, all corruption/bounds, every crash cut
and detailed editor/route fault permutations are unselected; their existence
adds no execution gate. Scenario12 explicitly selects four cuts and four reuse
arguments rather than invoking the entire old loop. Three selected denial
arguments reuse adequate assertions without executing all historical denial
permutations. Paused unit modules are not collected.

`web/scripts/identity-names-e2e.mjs --base-url http://127.0.0.1:<owned-port>
--out <owned-directory> --scenario agent|founder|picker` uses the existing
`playwright-cli` and screenshot harness `capture`, with actual served SPA/API;
`modeAProdApi` is not DB evidence. The picker path captures real agent/settings/
threads/tasks routes at390x844 and1440x900, en/zh-CN, light/dark. Screenshots are
read/draft-only reuse of that interaction, not extra business scenarios. Tooling
provisioning is venue-only `npm install --global @playwright/cli@0.1.18`, then
`node /usr/local/lib/node_modules/@playwright/cli/node_modules/playwright/cli.js
install --with-deps chromium`; its package explicitly pins Playwright
1.63.0-alpha-2026-08-05. No repository dependency/lock change follows.

Both `uv run python scripts/generate_openapi_snapshot.py --check` and real HTTP
`test_served_openapi_matches_supported_snapshot` are selected obligations; the
snapshot remains hand-authored until actual execution confirms it. Hosted
Ubuntu operation/workflow source is installed as `scripts/identity_names_hosted.sh`
and the existing nightly workflow's explicit `naming_only` job. EM TASK10343
approves this bounded naming operation under THR293seq14/22/34. It requires
`all_only=false`, exact published `naming_source_sha` and a real immutable
linux/amd64 `naming_image`; the broad manual lane is suppressed only for naming,
while default/schedule behavior, active checks and unit suspension remain.
Normal hooked publication and actual disposable source/image readiness precede
execution; installation or publication alone is not behavioral acceptance.

Both local-driver tmpfs volumes remain mounted by one offline, capability-free
holder through init/prepare/work and complete evidence export. Each real stage
checks source/evidence sentinels, cgroup v2 and actual tmpfs byte/inode quotas.
The operation records admitted Docker>=26, its exact version's Moby local-driver
source, <=2GiB immutable base input, Python3.14/Node24 and locked dependencies/
venue-only browser pins. No local host image pull or workload launch follows.
One holder0.05CPU/64MiB/16processes and one serial payload1.95CPU/2560MiB/496
stay within2CPU/512processes. Reserving all persistent5GiB scratch plus192MiB
evidence in addition to payload/holder memory totals7936MiB<8GiB. New tmpfs
pages also charge the writing payload's cgroup and may fail its2560MiB limit;
filesystem capacity supplies no extra memory. Scratch has200000 inodes and
evidence10000. Work files have8MiB limits; complete stage/control output is
bounded and any cap or retained-tail marker refuses acceptance. Rotated Docker
logs are diagnostics only; screenshots and failed nodes cannot be silently dropped.
The runner starts its common27-minute deadline before checkout/tool acquisition,
reserves120seconds for export and all remaining-time stop/inspect/remove/absence
calls, and leaves three minutes of the30-minute job for upload/runner overhead.
The holder is removed after export and other containers, then exact volumes.
Workload failure remains the operation exit if cleanup/export also fails; those
failures are separately recorded and turn an otherwise-successful operation nonzero.
Export contains at most256MiB/14000 entries; incomplete proof is never PASS.
All new scenarios/browser/captures/schema checks remain **AUTHORED / NOT RUN**.
Python units are SKIPPED/SUSPENDED THR291seq5/16; broken general integration is
SKIPPED THR243seq42, never PASS. Historical JOB4075 frontend-all exit0 applies to
its predecessor bytes only. Full same-head independent review, behavioral QA
(revisit10329 through recorded supersession), current checks/hooks and guarded
manager merge remain outstanding.
