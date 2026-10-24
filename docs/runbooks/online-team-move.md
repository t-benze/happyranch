# Online consultant move: engineering and Founder handoff

Current source contract: [approved THR296seq135 plan](../superpowers/specs/2026-10-14-online-team-move.md),
sole root TASK11345. This runbook is engineering-owned. Founder need not prepare
an offline manifest, hand-edit YAML/AgentDefs, choose native file paths or run
private Python helpers. [PR1022/1052 offline maintenance](human-team-roster-migration.md)
is historical/deferred and supplies no online readiness or execution evidence.

## Source, release and operation gates

F proposes final source enablement (`APPLY_AVAILABLE=True`, empty
`NOT_INTEGRATED`) together with native schema preparation and actual daemon
readiness checks. This unmerged candidate is not accepted or activated capability.
Independent FULL review of the ENTIRE composed feature, same-final-head expressly
SOURCE/POLICY/STATIC QA, lint/static checks and manager guarded merge must assess
this enablement in the same PR. Prior slice verdicts do not accept F. Source/static
acceptance supplies no behavioral acceptance. No runtime flag, environment opt-in,
private bypass, request field or fabricated acceptance marker enables it.

A separately authorized release/host owner loads the reviewed release through
the supported guarded launcher. Loading/restart is not performed by this task.
Engineering owns optional-table preparation through normal `OrgState.load` under
that release operation's exact authority. Strict read-only move discovery runs
BEFORE naming preflight, generic compatibility writes, partial-roster validation,
publication and producer admission. After complete native workflow validation,
the existing DB-owned `install_team_move_schema` transaction validates org identity
and exact existing layout/history, refuses held/unknown state, installs the ONE
literal table only when absent and validates before COMMIT. Repeat compatible
attachment is idempotent. This does not upgrade reserved workflow layouts.
Generic Database/runtime-audit construction, capability/preview/show and restricted
diagnostic recovery never install or repair it. An unresolved operation remains
diagnostic-only before normal attachment. `team_move_schema_absent` is an
engineering loading/preparation dependency; Founder runs no private helper,
manual SQL, file assembly or offline manifest. Complete independent old/new
release references and absence compatibility remain; even an empty installed
table requires compatible reader@3. There is no general migration command.

For a separately authorized source release on the shell installation, the release
owner selects the reviewed compatible checkout using the existing release process
and uses its supported `scripts/daemon.sh start`; it passes
`--reader-guarded-start=online-team-move@3`. `scripts/daemon.sh status` observes that
launcher. Start does not replace an already-running daemon: any required stop/load
belongs to the separately authorized external host owner. The native Mac app's
bundled and development launchers pass the same protocol. Runtime selection uses
the existing `happyranch use /absolute/registered/runtime` boundary, which checks
source and destination holds; it is not a schema-repair workaround. No such
command was run here. Engineering then reads the loaded public capability below;
installed Git identity or a healthy launcher alone is not loaded-SHA attestation.

The exact live preview/apply/readback and ordinary consultant thread observations
need their own explicit operation authorization. Source approval alone grants
neither runtime loading nor that live operation. Installed source SHA is not
loaded daemon SHA; report loaded identity UNKNOWN without actual attestation.

## Capability and supported commands

The following command forms are source-supported; none were run against the live
feature here. Local argument validation precedes connecting. Outputs are JSON,
with actual `http_status` and the unflattened service result/error envelope.

```text
happyranch teams move capability --org happyranch
happyranch teams move preview --org happyranch --from consultant --to default --agents consultant_head consultant_codex --demote consultant_head --retire-source --drain-threads
happyranch teams move apply --org happyranch --operation-key KEY --expected-digest REQUEST_DIGEST
happyranch teams move show --org happyranch --operation-key KEY
happyranch teams move show --org happyranch OPERATION_ID
happyranch teams move recover --org happyranch OPERATION_ID --expected-revision REVISION
```

Each level has standard `--help`. Capability reports version1, source enablement,
effective `apply_available`, `recover_available`, their distinct incomplete-reason
lists, optional schema/attachment state, current active operation and required
reader `online-team-move@3`. Apply needs the actual matching DB/coordinator/roster,
profile coordinator, live host admission, actual tracker binding, ready naming and
matching current authority snapshot. A held operation or running service owner
prevents new apply. Recovery retains schema/native owner prerequisites while
allowing diagnostic-only attachment and fenced publication; native previous-owner
proof/CAS still decides whether any recovery may proceed. Same-operation running
recovery remains a show/202 response. These observations reserve nothing and
certify no idle roster. An old daemon's404 is unsupported capability,
not a busy operation or success. Read capability again after authorized loading;
do not fall back to the offline utility or a private apply helper.

Missing schema, profiles, host/tracker or matching coordinator belongs to the
engineering release/attachment owner. `diagnostic_org_requires_recovery` directs
the admitted operator to show and explicit SAME-operation recovery, never normal
attachment of partial files. `team_move_authority_not_ready` preserves a bounded
native `authority_unavailable_code`; resolve that publisher through its own owner.
`team_move_names_unavailable` requires native naming readiness, not a name rewrite.
The operation's `team_move_current_names_unavailable` also requires actual native
metadata for both agent IDs and the distinct Founder identity. Consumers use
`(kind, canonical_id)` keys. Initial and stable naming digests cover every native
Subject record in sorted native-key order as a strict canonical JSON list,
including unrelated identities and all six metadata fields. Public current names
come from a separate native read and refuse missing agent labels; that read does
not prove coherence with the earlier operation snapshot. This source correction
does not rewrite saved requests or establish full planner/service/HTTP behavior.
Reviewed source, installed checkout and loaded daemon identity remain separate;
preserve the SAME saved key/body and existing custody on any failure. Runtime
adoption and actual preview/apply remain with their authorized release/Founder owners.
Stopping/busy owners must finish their real lifecycle. Admission refuses503 with
`team_move_capability_unavailable` and actual `incomplete_reasons`; no unknown is
treated as availability. Move-specific task/thread/job/cleanup blockers still
come from the complete preview/final census, independently of capability.

Native naming resolves each human argument to an active permanent ID before
normalization/digest. Current ASCII names and permanent IDs are accepted; former
names refuse409 with current name/ID, unknown/ineligible names refuse. Team scope
is exactly Consultant to existing human-managed Default. The request must bind
both consultants, explicit head demotion, source retirement, the exact approved
charter and chosen bounded thread-drain consent. No rename, manager election,
reprovision, active-task transfer or Engineering routing change is supported.

## Preview and blocker resolution

Engineering prepares/reviews the admitted exact preview: canonical diff and
before/after digests; current names; native instruction/settings/permission/skill
and launch-input deltas; authority/profile/dependency/policy inputs; generated
manifests; complete actionable blocker kinds/IDs/owners; pending thread
obligations and drain candidates; global publication/current-generation impact.
No presentation cap can declare a partial census complete. Unsupported topology,
unknown ownership or new grants refuse. Private canonical images are not sent
as request fields or returned by show. Preview calls only D's pure native
selection/rendering path, without adapters/materialization, stores/packages,
reset, publication or reservation.

The launch-input manifest selects native `cli=True` allow-rule syntax, matching
both native executor launch consumers. Settings retain the native `cli=False`
projection. These selectors preserve the existing two permission surfaces.

`inventory_complete:true` describes that observation, not future quiescence.
`can_apply_now` also requires enabled capability and no blockers.
`can_drain_threads` identifies already-running native thread candidates; it does
not certify hard blockers gone or guarantee completion. Apply recaptures the
actual running turns after reservation and repeats the full mutable census.
Only these turns may finish their genuine callback/cleanup under old context,
with at most30 seconds of drain. Pending tokens/ranges stay pending.

Startup and archived-thread resume cutover share a lazy, fully materialized
release view and common lifecycle-family decoding only within one transaction's
mutation-free segment. Unbuilt differs from proven-empty; every write invalidates
both views and selected conclusions. Initialized pairs still pass evidence and
permission checks before the no-op shortcut. Common sibling shape/size/duplicate/
org/thread/audit-owner checks remain complete; selected initial/append/native
agent/session histories replay on demand in caller order. Ordinary absent history
remains None, distinct from bounded release missing/orphan/1024-row refusals.
Permissions, live owners, holds, recovery, Pause, effects and raw census stay fresh.
Terminal status alone is unsafe: callback consumption can precede cleanup, and
settled=true/quiescent=false or sticky failed_cleanup retains unknown. Pending
preservation still uses the stricter retry/deferral/live-owner predicate; nonpending
history must be producer-settled and known before it stops preserving. Other
callers reread normally. This is full common decoding with lazy selected replay,
not unsettled-only pruning or lifecycle clearance. A legacy triggering_seq0 write
followed by another participant's native_delivery_range refusal rolls back the
whole cutover, without reusing the prewrite view or changing resume's prior commit.

Resolve hard blockers through their actual owners: pending/paused/blocked tasks,
parents/children/chains, jobs/waits, workflow/review/revision/staged callbacks,
remote attempts/workspaces, profile/publication operations, enrollment, native
writer tails and uncertain process/cleanup evidence. Pause or a terminal row is
not clearance. Never auto-cancel tasks/jobs, fake callbacks, clear uncertainty,
kill processes, transfer active work, consume future slots or copy policies to
make a preview pass. Stop assigning new work to the pair while preparing an
authorized drain; avoid wake-generating progress to them. Unclaimed obligations
and native missed-slot/expiry rules remain intact.

Historical task attribution is separate from current launch eligibility. The
complete parent/child/revisit/chain/fanout/workflow closure retains task team keys,
recorded participants and permanent IDs, including retired actors authenticated
by the existing strict naming owner or native INTEGER task/result/SID attribution.
It never resolves settled history through today's manager or requires re-enrollment.
Unknown identity/graph facts remain actionable census blockers without discarding
collected inventory; dependent sessions/jobs/recovery callbacks/workflows remain
joined even for terminal tasks. New execution still requires a current executable
owner. No historical record, team, callback or binding is rewritten.

The SAME INTEGER thread invocation/agent/SID preparation owner spans native
materialization, integrity, repository refresh and launch-spec/cache preparation,
including helper-internal omissions and caught failures. Such uncertainty appends
the existing sticky `failed_cleanup` fact BEFORE ordinary failure settlement;
backend preparation/cleanup and native workspace/cache failures retain ownership.
Later success/callback/terminal state cannot erase it. Serializer, audit COMMIT,
strict history reader, restart/reentry and census preserve that fact, independently
of frozen EMPTY/nonempty retries. Ordinary stale-claim/agent/breaker or missing
launch-seam refusals before effects retain their native no-effect disposition;
returned failure alone never proves compensation. No new schema/journal/backfill
or generic producer identity is introduced. All behavior remains UNEXECUTED.

Ordinary tasks carry their SAME native TaskInvocation/task/agent/runtime SID
through materialization, integrity (including retries), package omissions,
repository refresh, attachment writes/cleanup, task scratch and builtin/custom
cache/workspace setup. Safe deferred ownership becomes durably `prepared` before
preparation resumes; this never clears existing uncertainty.
Preparation failure sets the existing local reservation cleanup flag BEFORE the
fallible strict Pause save/COMMIT, then retains `execution_unknown`/`unknown` in
the admitted journal. Terminal/callback/quiescent later attempts and safe unrelated
worktree cleanup cannot clear it. Settlement persists that fact before release;
failed saves retain local ownership and original plus persistence errors.
Restart treats unclosed task `prepared` entries as unknown, including terminal
rows and prepared recovery/retry owners; absence of first launch is no proof of
preparation cleanup. Unknown deferred entries cannot authorize a fresh launch.
Native pre-effect source/artifact validation, repo-scan and missing launch-seam refusals
retain their no-effect disposition. Existing retry/recovery IDs, frozen budgets,
INTEGER callbacks and authority-v2 are preserved. No new persisted field/journal, history
backfill, compensation or clearance writer is introduced. Behavior UNEXECUTED.

Async thread/dream/wake/Todo repository refresh runs off-loop with its SAME
native owner and a joined worker. A private org lifetime begins before native
reservation and ends after native finish, including failure. Ordinary unload
refuses active runners with409, retains existing task/team-move checks, and
fences old OrgState references before detachment. Runtime registration (same or
changed root), /runtime/use and their shared swap preflight ALL old orgs for
tasks, runner lifetime and team-move holds before closing any org or committing
registration/activation effects. Shutdown refuses replacement before construction.
Idle replacement closes/fences retired instances before publishing new OrgStates;
a busy later org leaves earlier orgs open and busy refusals return409.
Shutdown stops runner/new-org
attachment admission and joins actual runners through refresh and settlement
before closing databases; repeated cancellation during the join cannot skip it.
This is process-local lifetime bookkeeping, not durable clearance or process
quiescence proof. Earlier unknown history and persistence failures remain sticky;
all timing/race/shutdown/recovery behavior remains UNVERIFIED.

Exact diffs/manifests belong only in the admitted Founder's private client
session. Do not paste them into shared artifacts, logs or public PRs. They
contain no provider/runtime memory, credentials or raw invocation tokens. The
existing shared identity-less bearer and `_require_human` do not cryptographically
distinguish a person from another holder of that secret. Agent/session/invocation
proof and descriptive headers confer no additional authority. Engineering agents
must not use ambient credentials to self-admit the live operation.

## Stable request custody and exact Founder apply

Preview creates/prints a stable key, exclusively saves the local selection, then
saves the returned normalized apply envelope under the SAME key in
`~/.happyranch/client-team-moves/ORG/`. Client-owned directories are0700 and
files0600, no-follow/exclusive, with file/directory fsync. The saved apply envelope
contains no canonical images or arbitrary paths. This client bookkeeping is
separate from server preview purity and is not an OS barrier against same-UID
tampering. Every fresh save, identical-save reuse and apply read pins no-follow
directory descriptors from the client home, validates private ownership/type/mode
and stable entry/file identity, checks exact canonical bytes, fsyncs and closes
the exact file, then fsyncs the containing directories bottom-up and closes them.
Directory reuse repeats persistence as well as creation, including an earlier
failed mkdir/fsync/close. All persistence/close errors refuse before transport.
Partial, noncanonical or changed bytes remain preserved and refuse without
overwrite or a replacement key. A complete file from an earlier failed save may
proceed only after this positive boundary succeeds again; existence/JSON alone
never establishes custody. This protocol does not automatically resend anything.

Founder reviews the displayed exact diff and `request_digest`, then explicitly
runs apply with that key/digest. The CLI rereads/validates and establishes the
file/parent durability boundary for the exact saved bytes before connecting; it never asks Founder to hand-edit files. `--drain-threads`
belongs to the preview consent and cannot be changed on apply. Same-key identical
normalized body returns original state/receipt without another reset,
materialization or publication; changed body refuses409. Stale preview refuses
before forward decision. A new key can reuse a genuinely completed request only
after full native current role/context/authority/census verification, returning
the ORIGINAL ID/key/receipt plus `requested_operation_key` and
`already_satisfied:true`; no new alias row or key reservation is written.

On an apply transport error the CLI performs one GET by the SAME key. A saved
COMPLETE must also match the locally frozen public request digest before exit0.
A missing/lost/malformed response never manufactures completion. Preserve the
key/bytes, use explicit show, and never generate a replacement key or automatically
send another apply. A new preview after an explicit precommit refusal is an
operator decision; an unknown/committed operation must retain its original key.

HTTP202 plus Location and CLI exit3 mean accepted/in-flight. Do not promise
duration or call it success. Only a saved genuine COMPLETE receipt/readback and
released active slot permit apply HTTP200/CLI exit0. Refusal/repair/disabled is
exit1, local argument/custody validation2. Show may return202 for a live owner or
409 for refusal/repair/interrupted ownership. Both GET by-key and GET by-ID
declare202 in served OpenAPI and the existing inventory, with private no-store
headers and Location. Responses retain the exact saved state, key,
ID, revision and next action. It performs no advancement or writes. There is no
indefinite polling. HTTP failure status and structured `detail` remain visible.

## Recovery, attachment and readback

The daemon service owns strong registered bounded tasks across client disconnect,
not a model session or unowned coroutine. Native awaits have a45-second service
deadline, including the <=30s drain; this is not a hard filesystem-duration
guarantee. Shutdown cancels/reaps owners before host/DB closure. Timeout,
cancellation or process loss never erases committed holds. Failed precommit
WAITING may be explicitly abandoned through recover after native unchanged-state
verification; committed operations recover forward only.

Explicit recover binds the SAME operation ID and observed expected revision.
Native owner-finished evidence or actual prior-process identity loss plus CAS
governs takeover; elapsed TTL, missing tracker or caller Boolean cannot. No
automatic retry, journal adoption or second unchecked apply occurs. If an owner
is still live, show reports that state. Unknown staging/acquisition/cleanup or
old-history gaps retain actionable repair_required, exact owner IDs and holds.
Do not glob/delete stage files or rewrite history/receipts to obtain release.

Read `saved_owners` for original and latest claimed recovery IDs, the previous
claim owner and original publisher journal/writer. These are strict saved
identities, not a current liveness assertion. `diagnostic` preserves the existing
closed code and up-to64 safe owner IDs at the saved revision. This bounded list
is not a complete blocker inventory. Recovery census refusal records useful IDs
through that existing strict CAS/history path only after actual prior-owner
settlement proof; a refusal before proof cannot claim takeover or rewrite history.

`current_observation` separately reports native blocker kind/owner ID, agent/thread
IDs where known and the native remedy, plus current publication journal/writer,
recovery/file-phase owners and generation/state. It reads the actual native
owners without ordinary attachment or preview. A diagnostic-only org uses a
read-only restricted context; absent trackers produce unknown owner blockers.
Unavailable census/publication facts are null with a safe code, never empty/idle.
A close error is secondary to the retained observations. Resolve the named task,
thread, job, process, profile or publisher through its own supported owner before
explicit recovery of the SAME operation/key/target at the fresh observed revision.
No TTL takeover, automatic retry or adoption of a competing journal follows.

`service_diagnostic` preserves safe asynchronous refusal blockers, owner IDs,
failure-time native observation and secondary close/reattachment codes. It is
explicitly process-local and non-durable; it may disappear on restart. The strict
saved diagnostic and immutable decision/progress identities remain readable
independently. Historical saved facts never masquerade as current observations.
Raw exceptions, canonical/proof bytes, process start-identity proof and invocation
credentials stay private. Reattachment failures keep the saved receipt and
refusal state instead of retrying/resetting/publishing the move.

An org held outside normal OrgDep remains reachable through the same admitted
capability/show/recover service. Saved show opens a strict read-only existing
database; it does not bootstrap, migrate or attach. Recover uses D's restricted
context with actual daemon host/tracker ownership and exact images. It closes
that context before ordinary `add_org`. Positive discovery occurs outside leases;
the native short attachment segment revalidates the opened owner/history/hold;
only successful synchronization/shared-map insertion clears diagnostics.
Failures retain diagnostic roots. After successful attachment/release, real
deduplicating task/thread/producer owners redeliver; schedules retain ordinary
due/missed rules. A saved terminal receipt stays readable even if attachment
failed, and explicit recovery can retry attachment without resetting/publishing
the operation again. Engineering resolves unavailable native owners through their
existing release/repair processes, not an arbitrary DB/YAML edit.

Engineering's separately authorized readback must establish exact registry and
AgentDefs (both workers, no active Consultant, Default human Founder); unchanged
IDs/current names/paths/history/frozen bindings/executor/model/allow rules and
Engineering omitted-owner routing; both operation-scoped native reset audits or
genuine zero-row evidence; current native context/settings/all-context skills /
permission/integrity evidence; SAME publisher journal/cache and ready pointer /
file/digest; COMPLETE/release and actual obligation delivery. Ordinary subsequent
Founder-addressed consultant turns observe fresh worker context without protected
denial attempts. Preserve private memory. Each absent observation stays missing.

Updated shell and native Mac launchers require reader@3 (reads old@1/@2).
It covers explicit C2 retry initialization, including an EMPTY budget. Old missing
facts remain readable without backfill; ambiguous pending retry/reentry refuses
`team_move_thread_retry_initialization_unknown` and remains a native blocker.
Engineering resolves that retained owner through its existing process; never
delete audits, replace a token/SID or retry with a fresh budget to clear it.
Empty optional layouts still need compatible code. Already-running old daemons,
obsolete launchers and unchecked old interpreters are outside the cooperative
guard. No public undo or old-reader downgrade is safe for a durable decision.
File rollback cannot reverse audited resets, context/package effects or
publication history; whole-DB restore/reverse moves need separate authority.

## Exact JSON contracts and evidence limits

`runtime/orchestrator/team_move_public.py` defines the closed strict schemas;
`runtime/daemon/routes/team_moves.py` inlines the same body schemas in served
OpenAPI. Unknown/duplicate members, invalid UTF8/BOM/nonfinite JSON, binding
fields, actor overrides, arbitrary paths/roles/permissions and all query
parameters refuse before reservation. No frontend flow is added.

| Method/path under `/api/v1/orgs/{slug}/team-moves` | Exact request |
| --- | --- |
| GET `/capability` | No body/query. |
| POST `/preview` | `version:1, source_team:"consultant", target_team:"default", affected_agents:[ADDRESS,ADDRESS], demote:ADDRESS, retire_source:true, charter:EXACT_TEXT, drain_threads:BOOLEAN`. |
| POST root | `operation_key:KEY, request:PublicMoveRequest`. |
| GET `/{operation_id}` or `/by-key/{operation_key}` | No body/query; original saved state/receipt only. |
| POST `/{operation_id}/recover` | `expected_revision:POSITIVE_INTEGER`. |

`EXACT_TEXT` is “Advise the founder and HappyRanch teams on product, services and
business operations. You are an individual consultant reporting directly to the
founder.” `PublicMoveRequest` is the normalized selection (agents ordered
`consultant_codex,consultant_head`, demote `consultant_head`) plus
`private_request_digest` and `expected`. The latter contains exactly
`canonical_digest, roster_digest, naming_digest, profile_digest,
dependency_digest, policy_digest, skill_selection_digest, permission_digest,
authority_namespace, authority_generation, authority_digest, agent_revisions`.
These are digests/namespace/positive generation/two ordered digest values, never
request-selected permissions or file paths. Preview `request_digest` hashes the
canonical normalized public request. The saved D receipt retains its original
private request digest; show reports both and the original receipt/digest.
Errors retain bounded `code`, truthful `committed`/`recovery_required`, selected
operation state when observable and next action; unknown state is null/uncertain,
never an invented uncommitted or success claim.

Acceptance is SOURCE/POLICY/STATIC ONLY under plan8, on SAME13 four-answer
observations. All absent HTTP/CLI/success/denial/migration/history/recovery /
process-loss/race/cleanup/startup/launcher/downgrade/reset/context/publication /
INTEGER callback/live/natural worker/memory observations remain UNEXECUTED.
General integration INCLUDING callback is SKIPPED THR243seq42. Python units are
RETIRED. No new cases/parameters/fixtures/replacement campaign/behavioral verifier,
ad hoc E2E, protected mutation/kill/replay/restoration, production feature DB
operation, live preview/move/reset, deployment/restart/shutdown or Jenkins ran.
Static success never supplies behavioral/test-authoring PASS or feature-complete
acceptance. Root owns independent F acceptance/merge and the separate activation,
exact Founder operation/readback and ordinary worker observation handoffs.
