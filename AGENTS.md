# Project: HappyRanch - Multi-Agent Org Runtime


### Bounded ordinary native launch refusals (THR-315)

Actual non-hold refusals before durable preparation allow at most THREE consecutive
attempts for the same logical task continuation. Existing audit rows
`task_native_launch_boundary` retain the original fingerprint/owner/agent/generation
key, actual reserved SID, invocation and attempt identities, cumulative count and
first safe cause; the latest three bounded payloads suffice without new schema or
journal fields. Reconstruction/redelivery does not reset this count. A genuine
positive-PID native launch callback records a zero-count boundary before fallible
phase persistence. A reserved SID and memory telemetry never prove launch.

Human-team failed-recovery history treats this exact launch/refusal audit action
as informational. It neither selects a result nor grants recovery authority;
selected result/session/worker/team and replay fences still apply, and unrelated
or unknown causal actions still refuse before terminal or parent effects.

An actual refusal after durable preparation fails earlier: the existing outer
settlement makes an unclosed preparation unknown, and unknown owners cannot safely
be adopted for another launch. FAILED with `launch_refused` uses normal failure,
parent enqueue and thread followup; it never clears `failed_cleanup`, unknown
journal/process evidence, retry lineage or worktree guards. Existing smaller host,
provider and recovery ceilings remain. Intentional Pause/team-move deferral,
child/job/capacity/backoff waits, shutdown and stale/current-owner loss consume no
allowance. Current-owner and authentic callback fences precede evidence and are
rechecked inside failure transition; later continuations have separate keys.
Terminal deferred owners are not republished, even when unknown evidence remains.

Each new retry or recovery launch boundary resets only local launch attribution
before fallible initialization; a previous positive PID cannot mask a new refusal.
Original launch/unknown evidence remains intact. Deferred owners are authenticated
read-only before adoption writes, then revalidated under the native writer. Refusals
retain the durable original SID/agent/generation/continuation; no fallback SID counts
for that owner. An unclaimed recovery uses its exact original-SID journal proof and
existing claim exclusions, never ordinary pending/carrier eligibility. Callback,
current-owner, cancellation and intentional hold winners still exclude accounting.

The first preparation cause is kept separately from the final Pause/cleanup error:
bounded stage, exception class/stable Pause code and source locations only. An
executed pull adds a safe repo identifier, fixed operation and return code or
exception class. Skipped pre-pull admission remains a stale-source warning and
creates no pull-failure cause. No exception messages, output, argv, URLs, environment
or locals enter this evidence. An audit/storage save failure remains an explicit
persistence limitation, not a durable count or terminal-success claim. No new timer,
service, automatic successor or permission follows. Source/scoped verification is
separate from deployment, running-code identity and operational acceptance.

## Integrated online team-move F candidate — THR296seq135 / TASK11345

The integrated F candidate supplies founder-controlled service/CLI source with
`APPLY_AVAILABLE=True` and empty `NOT_INTEGRATED`. This candidate still needs
independent whole-feature review, same-final-head SOURCE/POLICY/STATIC QA and
manager guarded merge. Unmerged source is not accepted or activated capability.
The actual daemon service independently checks schema, native org/coordinator,
profile, host/tracker, naming and current authority before advertising apply.
Explicit recovery uses the SAME native owner gates while allowing a diagnostic
org/fenced publisher; `recover_available` and `recovery_incomplete_reasons` are
separate from apply readiness. Neither projection certifies quiescence. Unknowns
refuse with actionable reasons; no flag/private bypass/acceptance marker exists.
Preview and saved-state reads retain the existing bearer + `_require_human`
boundary, whose shared identity-less secret does not prove human presence.

F native release preparation runs through `OrgState.load`: strict read-only move
discovery precedes naming preflight, generic compatibility writes, roster
validation, publication and producer admission. After complete native workflow
validation, the DB-owned `install_team_move_schema` transaction validates exact
existing layout/history/org identity, refuses held/unknown state, installs only
the literal ONE table if absent, and validates before COMMIT. Repeated compatible
attachment is idempotent. Generic Database/runtime-audit, capability/preview/show
and restricted diagnostic recovery never install or repair it. Source delivery
does not call it on production: separately authorized compatible release loading
through the supported launcher performs native preparation; Founder assembles
no files, SQL or offline manifest. Missing schema/owners are engineering release
dependencies. Even an empty installed table requires reader@3. An absent optional
layout remains compatible with generic/older readers; source rollback cannot
undo durable state or grant a downgrade.

F also freezes C2 thread retry initialization explicitly, including EMPTY
budgets. New initial/fallback/nudge phase initialization is distinct from a
frozen zero budget; deferral/reentry cannot replenish it. Old missing facts keep
their original history interpretation but refuse ambiguous new retry/reentry
with `team_move_thread_retry_initialization_unknown`; no history is backfilled.
Updated shell/Mac launchers require `online-team-move@3` (reads old @1/@2), so
earlier supported parsers refuse before startup. Already-running old daemons,
obsolete launchers and unchecked old interpreters remain outside that guard.
All behavioral/installation/recovery/launcher observations remain UNEXECUTED.
Agents must not self-authorize with ambient credentials. Manage-agent retains
team/role and cannot transfer agents.

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
retain their no-effect disposition. Repository refresh also refuses before pull
on detached HEAD or missing/unusable/unreadable upstream; skipped repos stay false
and warn of stale source. Every executed pull failure/timeout/exception retains
uncertainty, including dirty/non-fast-forward and recursive partial effects;
equal HEAD/branch/porcelain summaries never prove no effects. Read-only upstream
lookup is admission only, not a remote-state lock. All observations/pulls share
one 30-second batch deadline; exhausted/unattempted siblings stay false. Thread/dream/
wake/Todo await off-loop refresh under the SAME native preparation-effects scope,
joining its worker before cancellation propagates and settlement runs. Timeout
never certifies process-tree quiescence; existing unknown owners remain sticky.
The four async runner entrypoints retain a private org lifetime through native
finish (including failure), beyond the refresh Future. Org unload refuses active
runners and fences detached instances against late starts. Runtime registration
(including the same root) and /runtime/use preflight ALL old orgs for tasks,
runners and team-move holds before any close or registration/activation effect.
Shutdown refuses runtime replacement before construction; idle replacement closes
and fences every retired instance before publishing new OrgStates. A busy later
org leaves earlier orgs open, with busy refusals mapped to409. Shutdown stops runner
and attachment admission, then joins cancelled workers and native finish before
DB close; repeated cancellation during that join cannot skip it. No timeout
establishes quiescence, and failed persistence retains native uncertainty.
Existing retry/recovery IDs, frozen budgets,
INTEGER callbacks and authority-v2 are preserved. No new persisted field/journal, history
backfill, compensation or clearance writer is introduced. Behavior UNEXECUTED.

Preview resolves native current names/IDs, rejects former/unknown names, uses
only D's pure native planner and returns exact founder-only diff/digests,
context/permission/skill deltas, complete blockers/owners, drain candidates and
org-wide publication impact. It reserves nothing. Internal canonical images
stay private; never copy these admitted diffs to shared artifacts/logs.
Only already-running captured threads can drain (at most30s); pending tasks,
paused roots/children/jobs/workflows/remote/cleanup uncertainty remain blockers.
`can_drain_threads` identifies candidate turns, not clearance of hard blockers.

The client saves its exact normalized envelope and stable key privately before
POST. Fresh saves, identical-save reuse and apply reads all establish the same
positive custody boundary: pinned no-follow private directory/file descriptors,
exact canonical bytes and stable inode/type/mode/ownership, file fsync/successful
close, then bottom-up containing-directory fsync/successful close. Existing
complete bytes after a failed save proceed only after this boundary succeeds;
partial/changed bytes remain preserved and refuse without overwrite/new key.
The CLI prints the key and retains it on uncertainty. Same-UID races remain a
cooperative-storage limitation, not an OS isolation guarantee.
Apply transport loss performs one same-key GET, never a second POST/new key.
HTTP202/CLI3 means accepted/in-flight; only saved COMPLETE/readback/release yields
apply HTTP200/CLI0. Refusal/repair is CLI1; local argument validation is2.
Show never advances/writes. Explicit recover uses the same ID/expected revision,
native previous-owner/process-identity proof and CAS, with no TTL takeover.
New-key already-satisfied verification requires a prior genuine COMPLETE plus
full current native context/role/authority/census verification; no alias row,
reset, publication or invented receipt is written.

The daemon owns/reaps bounded tasks across client disconnect and before shutdown
DB close; timeout/cancellation never erases committed holds. Diagnostic-only orgs
have strict read-only saved-state access outside OrgDep and use D's restricted
native recovery owner. Close that context before positive ordinary attachment;
only successful native discovery/revalidation/map insertion clears diagnostics,
then real deduplicating owners redeliver.
Saved `diagnostic` retains the strict code/at-most64 safe owner IDs and original
history; it is a bounded refusal record, never a complete current census.
`saved_owners` derives original and latest recovery owner and original publisher
from strict decision/progress. `current_observation` separately reads native
blocker kind/ID/owner/remedy and current publisher/recovery/file-phase owners;
unknown facts stay null/unavailable, with no idle certificate. Diagnostic-only
orgs use a read-only restricted native context, without attachment or mutating
preview. Missing trackers remain unknown. `service_diagnostic` carries safe
asynchronous failure facts and secondary close/reattachment codes, explicitly
process-local/non-durable; restart loss never erases saved IDs/diagnostics.
Prior-owner proof/CAS still governs recovery. Native census refusal saves bounded
IDs only through the existing strict repair transition after that proof; show
never writes. Resolve the named native dependencies before explicit SAME-key,
SAME-target recovery at the newly observed revision, without retry/takeover.
Both GET by-key and GET by-ID declare202 while the daemon-owned task runs; private
no-store/Location and CLI3 remain, with200 reserved for saved completed readback.

Org unload/runtime switch refuse active
owners/holds. D staging/history/inode/ENOENT cleanup, all-eight-site outside-lease
discovery, C1-C3 sticky uncertainty/EMPTY budgets, INTEGER callbacks/v2 and native
permissions remain intact. No new table/column, auth, permission or hook changes.

Follow [the online runbook](docs/runbooks/online-team-move.md) for the engineering-owned
F acceptance/enablement, separately authorized release loading and exact live
Founder operation/readback handoff. Source delivery is not activation or behavior.
SAME13 four answers remain; all absent HTTP/CLI/migration/recovery/race/cleanup/
startup/launcher/downgrade/callback/live observations UNEXECUTED; integration
INCLUDING callback SKIPPED24342, Python units RETIRED. No new cases or campaign.


## Jobs — automatic admission (THR309seq1)

Obtain required content authorization in a thread BEFORE submission. From a
genuine ordinary task/session omit review_required or set false; rationale is
optional. Parsed true refuses422 before allocation (CLI exit2), without silent
flip/retry. HTTP201/CLI exit0 is admission, never script success; inspect actual
reason/exit/output. Legacy true pending rows/history stay inert and visible as
disposition backlog. Manual /run is a nonexecuting410 tombstone; both CLI run
aliases exit2 and both Web details retain only pending Reject/running Stop.
Initial cwd/interpreter preflight allocates no job. Later authenticated false,
never-launched prerequisite loss alone may commit failed/spawn_failed with
NULL start/exit, genuine finish/diagnostic/agent audit and postcommit wake.
Preserve owner/session/Pause/possible-launch/race winners, existing running CAS,
limits and all-terminal resumption. No schema/auth/permission/credential/general
notifier change or deployment follows. Current contract: [Jobs](docs/superpowers/specs/2026-05-26-jobs-design.md).

**Independent THR307 nightly disk sweeper — Founder seq48–51, ROOT TASK11518.**
`runtime/daemon/disk_sweeper.py` adds machine-local03:00, default-dry-run, sequential
org sweeping under one1800s monotonic batch window, with an owned stoppable worker
process group. Only actual terminal-owner/terminal-root scratch and registered
clean retained-ref worktrees can pass fresh jobs/process/recursive-age/containment
checks; unknown retains. THR307seq85/88 includes superseded for BOTH owner/root,
skips recognized finished jobs before owner-lineage lookup, and permits only
exact unreadable systemd/(sd-pam)/sshd-session/ssh-agent comm identities with
stable identity/credential/census checks and actual summary reporting. Readable
candidate use and other uncertainty retain. Stable zombie threads skip namespace
and live-use reads while retaining identity/Z-state, UID and full census checks;
a zombie leader never skips live siblings. Missing non-zombie references retain.
Preserve manifests/locks/output, dirty worktrees and
branches. No legacy receipt/count/rotation policy is imported into this new
mechanism; legacy cleanup, flags and seven sessions are unchanged. Dry run never
chmods/removes/prunes. One JSON/existing-shaped audit reports attributable allocated
removals and unknown/partial/unreached scope, never physical freed-byte invention.
No activation/deployment follows source delivery. Read the [current contract](docs/superpowers/specs/2026-10-15-nightly-disk-sweeper.md)
and runtime guide before changing it; independent review/scoped disposable QA and
normal hooks/static checks remain mandatory. Python units stay retired.


**Disabled coding release catalog K0 — Founder THR139seq562/563, ROOT TASK10693.**
GET `/api/v1/orgs/{slug}/workflows/release-templates/coding/1` authenticates the
existing bearer and resolves an existing org before reading the constant package
resource. CLI `happyranch workflows release-template coding --version 1 --org ORG
--json` calls that GET and validates the same closed receipt. Exactly634 canonical
UTF8 bytes pin SHA256 `6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8`.
The matched `workflow-release-template@1` receipt exposes exact bytes/base64,
identity/version/digest and compiler/validator/source pins; readiness is ALWAYS
`{enabled:false,reason:"release_catalog_only"}`. Missing/unsupported resource
returns503 `resource_unavailable`; corrupt/noncanonical data returns500
`resource_corrupt`, without private paths. Unknown kind/version is404.
One wheel force-include pair feeds the unchanged shared frozen inventory; actual
installed/frozen loading remains unverified. Import/mount/GET never seeds,
imports templates, reads mutable readiness, changes workflow data or allocates
work. The ordinary org GET is classified included for unchanged native handling;
K0's explicit radius ships no TS/provider/browser initiation or stage prompts.
No J migration, setup/mapping/adoption capability, activation, execution, CI/merge
engine or live operation follows. Later ordered coding is separately scoped
under Founder562; historical document pins and exclusions remain intact.
V1 later merge requires exact independently reviewed head plus successful required
lint/static CI on that head, scoped QA/hooks and guarded manager merge; optional
target-base conditions cannot block v1. Artifact comparison base remains fixed.
See [the current K0 source contract](docs/superpowers/specs/2026-10-14-ordered-coding-workflow.md).
Python units RETIRED, integration/callback SKIPPED24342; behavioral/protected proof
UNEXECUTED/FOUNDER-WAIVED139429/430. Source review is not behavioral acceptance.


**Bounded repository artifact source K1 — Founder THR139seq562/329, ROOT TASK10693.**
`repository_artifacts.py` supplies closed canonical saved binding/adoption/presentation
readers; the private `repository_artifact_objects.py` supplies bounded no-follow
adopted-primary-store copies, sanitized typed Git reads, recomputed object hashes
and complete raw base/head tree manifests. Allocation failures are category-only
storage_unavailable with no launch or acquired directory. Every refusal retains
the parent's acquired private-directory identity for owner remediation; process
quiescence and omitted child metadata do not prove cleanup. Worker cleanup stays
alarm-bounded; the parent performs no recursive cleanup and accepts success only
after witnessing directory absence. Unknown launch/nested process/reap ownership
remains internal. Retained directory identity is not a claim of present storage.
Saved validation proves historical integrity only. OrgState mounts `WorkflowRepositoryArtifactService`; its current
resolve operation categorically refuses before decode or object I/O because K2b's
authenticated adoption/current authority producer is absent. No constructor flag,
caller Boolean, record/digest or injected callback authorizes content access.
K0 readiness remains false and code activation still refuses before allocation.
Current document formats/producers, routes/CLI/DTOs/schema/receipt/recovery and
native permissions are unchanged. Later coding writers must ship complete selected
read/stage/prelaunch/receipt/closeout consumers with before-I/O admission and
post-await authority revalidation. No live adoption/setup, execution or coding
readiness is delivered. Python units RETIRED; integration/callback SKIPPED24342;
behavior/security/protected proof UNEXECUTED/FOUNDER-WAIVED139429/430. Source-only
review/static checks cannot establish reader-security or behavioral acceptance.
See [the current K0/K1 contract](docs/superpowers/specs/2026-10-14-ordered-coding-workflow.md).

**Naming v1 core, API and routing source (THR-293 seq14/22/34).** One daemon controls each org.
OrgState admits the existing generic/workflow/canonical source before installing
an org-only, atomic three-table naming extension. Installer-only N-SAC-1 in
`runtime/identities/admission.py` checks selected/actual DB ownership, task/audit
shapes, closed naming interactions and unchanged workflow/Pause/move/naming
validators inside the existing transaction; it builds no whole-release reference.
Global workflow/legacy reference consumers remain strict and separate. Non-install
refreshes/readers keep their prior behavior. Finite synthetic verification uses
`uv run --frozen python scripts/verify_naming_structural_admission.py --case B-SA-01
--scratch NEW_ABSOLUTE_TASK_TMP_CHILD --evidence NEW_ABSOLUTE_EVIDENCE_CHILD`;
select B-SA-01 through B-SA-08 separately, per the fixture README. No test Python
imports, live DB input, repair, provider or daemon lifespan is supported.
Generic Database and
runtime-audit remain naming-free. Every naming-installed database, including F,
requires a compatible naming reader; explicit F/E/G/H/I workflow migrations preserve
naming rows/revisions and never install absent naming. Flat schema1 still refuses.
Current names and permanent former claims share the case-insensitive ASCII
namespace with present active/pending/terminated IDs and founder. Editable names
are 1–64 ASCII letters/digits/underscore/hyphen with a letter/digit first and no
trimming. An admitted `_worker` ID remains an owner-bound default/address; it is
not an editable-name exception or permission to admit longer IDs.

The internal rename service uses revision CAS and one atomic existing-shaped
founder audit; pure rename changes no task/session/workspace/permission/authority
binding. Supported lifecycle writers reconcile actual terminal canonical results
under their existing shared coroutine gate. Naming-unavailable blocks new ID
creation/rename, while original same-ID guards and callbacks keep their existing
behavior. Coherent naming never proves bootstrap, workspace rollback or cleanup
success. The bounded API source mounts org-local GET /identities, POST
/identities/resolve and PUT /agents/{agent_id}/addressable-name and
/founder/addressable-name. Strict rename requires only addressable_name and
positive expected_name_revision. Raw body/query task/session/agent/composer/
speaker/thread/invocation identity-key presence refuses before decoding, even
null/partial/malformed. Existing shared bearer cannot prove a human identity;
descriptive founder/name headers confer no authority. Reads preserve canonical
IDs and expose null/unavailable label metadata on naming failure; name revision
and canonical-definition revision are distinct. Resolution is diagnostic, never
authorization or reservation.

Routing source now classifies current names and permanent IDs case-insensitively
to canonical agent IDs using explicit prose @tokens. ASCII quotes remain prose; explicit blockquote lines are literal; email/word interiors, escaped tokens and supported code are literal. Any
recognized former token rejects the entire current
message/recipient input with409 former_name and current_name, before thread-ID
allocation, exchange closure, callback settlement/consumption, message/audit/queue
mutation or application attachment finalization. Composer, speaker, task/session
and invocation fields remain ID-only; labels confer no authority. Current names
of pending/terminated owners do not designate wake targets. Agent unknown/self/nonparticipant-only intentions retain conditional fallback;
Founder unknown/nonparticipant intentions refuse422.
Unavailable naming preserves actual ID behavior; non-ID addresses refuse503
naming_unavailable. No read adapter reconciles or writes names.


New-message body admission excludes email/word interiors, escaped tokens and
supported inline/fenced code; ASCII quoted prose remains eligible; explicit blockquote lines are literal. Literal-only
bodies perform no body naming lookup. Genuine body aliases fail closed on service
failure, including empty explicit recipients and per-token unavailability. Known
actual canonical IDs retain owner checks; ID-shaped aliases supply no evidence.
Observed Former refusal precedes later chunk failures. en/zh-CN service wording
asks for retry or a known intended-recipient ID, with no body rewrite, automatic
retry/send or locale-triggered effect. Historical messages/delivery and release
measurement keep their original interpretation. The exact finite scanner contract
is in the current identity-names core spec (THR293seq174).

**Current naming CLI/prompt contract.**
Operator `identities list/resolve/rename` uses existing API and name-revision CAS,
local validation and uncertain-failure readback without retries. Human targets
resolve current names to IDs; founder recipients retain `@founder`. Historical
ID filters and naming-unavailable ID access preserve owner checks. Every CLI
managed associated upload checks former body/recipient tokens first, using
explicit prose tokens (email/code are literal), without rewriting bodies or
claiming reservation/
rollback across HTTP requests. Task/manager/full/resumed/fallback/dream/wake/
schedule prompts receive current self/founder/ID-selected roster labels; metadata
failure renders ID-only context without writes or launch gates. Automation,
callbacks, delegation/chain/fanout/config/roles and provider/task/session IDs stay
ID-bound. The provider-aware UI/types, editors and name/ID pickers are present.
Source-pinned maker receipts record actual scenario5/8/9 E2E and frontend outcomes
in the current spec and maker handoff; independent acceptance remains separate. See the current naming spec and maintained guides for the
combined source contract and remaining behavioral acceptance gates.


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
case matrix. The finite naming selection includes routing bodies for5/6/7/9/12;
original manifests remain historical proposed work. Python units and their collection are
RETIRED under THR-291 seq40, never PASS and never a gate. General broken integration is
SKIPPED THR243seq42. Naming E2E uses a supported disposable GitHub runner or Mac
Linux guest with finite commands/resources and actual cleanup. Source-specific
maker receipts and remaining independent gates are recorded in the current spec
and maker handoff; source publication does not grant feature acceptance. This
combined candidate includes CLI/prompt and provider-aware UI/type source with
English and Chinese editors, labels and pickers. Supported frontend checks run
in the guarded worktree; exact command/runtime/source/exit receipts and remaining
source-pinned E2E/browser/schema receipts and independent gates live in the maker
handoff. Final whole-candidate
independent reviewer APPROVE, focused behavioral QA PASS, applicable checks/hooks,
normal publication and guarded manager merge remain. Source integration and
mock-backed frontend checks do not establish whole-feature acceptance.

See the [current naming contract](docs/superpowers/specs/2026-10-09-identity-names-core.md).




**Mounted generic owner review — THR139465/480/493/506, ROOT TASK10693.**
The normal `/orgs/:slug/reviews` list and `/orgs/:slug/reviews/:activationId`
detail use owner-selected discovery and the existing recipient-safe reads.
Template pins supply output/role/outcome labels; there are no template-name
branches. Exact selected identity, canonical base64, strict UTF8 and SHA256 are
validated before rendering raw text. Current r1/r2 and read-only History remain
separate; an individual recorded response does not establish joined approval.
Only current collection validity with approved_use establishes approved input.
Explicit Request changes or Sign off requires exact nonempty operator findings
and a matching @1/@2/@3 command. A synchronous latch precedes hashing; one
closed key/body is frozen in sessionStorage before POST. Ambiguous sends lock
editing/opposite effects and offer only explicit same-command replay after a
fresh selected read, including reload. No effect/poll/locale change auto-sends.
Current read/refetch errors gate effects; access refusal hides selected bytes,
drafts and receipts and evicts sensitive selected cache/envelopes. Query identity
includes org/activation/lane/object; outgoing reads abort, panes detach, and late
hash/mutation results cannot affect a new selection. Locale changes preserve
mounted drafts/focus/selection without network work.
Return to author is a separate confirmation following full server readiness;
POST independently rechecks the original r1 CAS and authority. Genuine successor
and new author task IDs/status come from the server, with no optimistic r2 bytes
or receipt. Fresh r2 responses are required; further revision is unsupported.
Frozen original owner-return replay uses current access, identity and authority
admission separately from new-effect readiness. A pending genuine successor's
held/noncurrent collection permits only explicit identical acknowledgement
recovery; it never permits a new command. Current graph/status comes from the
selected successor independently of retained r1. Pending author completion does
not mean r2 reviews closed; unsupported further revision appears only for a
current objected r2 graph. Selected-read start/refusal or snapshot change
invalidates the admission token synchronously; the writer rechecks that token
and selected identity immediately after asynchronous command validation.
No raw proof editor, peer cancellation, author staging, direct submission,
r3/reassignment, execution/merge or digest-derived access controls are mounted.
This frontend source requires the compatible discovery/revision readers; old
daemon errors remain visible dependencies. No backend/native/schema/auth/shared
transport/dependency/hook changes follow. Bounded intercepted frontend rendering
and assertions establish UI behavior only; independent final-head review/scoped
QA and the assigned public synthetic two-template journey remain separate gates.
Real Founder Request changes AND Sign off and genuine agent judgment remain
UNOBSERVED/root-owned. Python units RETIRED; integration/callback SKIPPED24342;
broad/protected proof UNEXECUTED/FOUNDER-WAIVED139429/430. Required automated
CI/hooks remain Source Boundary Lint + Web Lint(Node24), static only.


**Document revision C1 — Founder THR139seq493/490, ROOT TASK10693.**
The same current Founder owner explicitly returns a fully collected, objected
r1 to the same author with a pinned CAS command. Pending peers survive objections.
POST `/api/v1/orgs/{slug}/workflows/instances/{instance_id}/revisions` takes
`workflow-document-revision-command@1`: operation_key, instance_id, activation_id,
expected_revision=1, predecessor_submission_id/digest, predecessor_round_id and
original authority pin. It returns201 or exact replay200 without another task.
GET `/workflows/revisions/{revision_operation_id}` selects exactly owner-human
bearer or the new-author current session; its closed receipt separates retained
identity/history from current eligibility and exposes no claim/host controls.
The new genuine task owns a new context/intent with admission_kind=revision,
attempt/generation2 and unchanged activation1, owner/binding/authorization/root.
POST `/workflows/revisions/{revision_operation_id}/submissions?session_id=CURRENT`
stages `workflow-author-revision-command@1` with literal2 and artifact_kind=document.
CLI `workflows revisions start/show/stage` matches these transports; stage requires
loopback session proof, without bearer fallback. Browser exposes owner/read only.
Staged G bytes keep permanent NULL legacy result; only the new task's own full
INTEGER callback and successful Boolean-quiescent terminal append the genuine
result link and fresh configured review requests/agent tasks. Review scope,
human/stage commands, receipts, acknowledgement and service join select explicit
@3/r2; old @1/@2 remain literal r1 and byte-compatible. Each current reviewer gets
only its own bounded r1 prior_review. The author gets all closed findings and
only originally addressed inputs. Fresh all-slot approvals yield one service
join; old receipts and tasks never approve r2. Historical r1 is needs_revision
with reason noncurrent_successor after return; current assignment replacement
retains all historical tasks/results/evidence/contributors and receipts.
Layout I changes only the draft admission enum and adds ONE
workflow_document_revision_operations table. F/E/G/H independent readers remain;
H literal r1/unique intent and every old schema/data byte are preserved. Proven
fresh org creation initializes I. Existing startup/reopen/enable only validates.
Explicit owned idle H→I operator source is
`scripts/migrate_workflow_document_revision_schema.py --org-root ABSOLUTE_ORG_ROOT
--org-slug ORG [--check]`. No live migration was executed. Even empty I needs a
compatible reader; no old-reader downgrade claim. Native denies the exact new
author POST using independent raw/normalized one-pass candidates before credentials;
shared normalizer/transport/auth/host permissions remain unchanged.
C1 source is delivered; wider recovery/reassignment/owner transfer/r3/direct-submit,
B2 repository resolution and mounted end-user UI remain separate. Code activation
still refuses409 before allocation. All new behavior/assertion/red-green/mutation/
repetition/proof/executable-QA is UNEXECUTED/FOUNDER-WAIVED139429/430; integration
SKIPPED24342; Python units RETIRED. Retained source assertions have not executed.
Source checks establish no behavior or migration safety. Independent full review,
same-head scoped QA, lint/static gates, normal hooks and manager guarded merge
remain. Founder465 public two-template/revision smoke and actual Founder UI
Request changes AND Sign off remain root-owned UNOBSERVED. This current C1
section supersedes historical C1-pending/fresh-H checkpoint descriptions below.


**Current reviewer staging — Founder THR139seq493/475, ROOT TASK10693.**
POST `/api/v1/orgs/{slug}/workflows/reviews/stages?session_id=CURRENT` and
`happyranch workflows reviews stage --org ORG --session-id CURRENT
--from-file /absolute/command.json [--json]` expose the existing current
non-recovery agent reviewer writer. Selected immutable request scope chooses
`workflow-review-stage-command@1`, `@2` or revision2 `@3`; old families remain closed and byte-compatible.
The five-field @1/@2/@3 acknowledgement (format, stage_event_id, request_digest,
replayed, signed=false) is unsigned. Genuine INTEGER/full callback and successful
Boolean-quiescent terminal under current authority remain necessary for a receipt.
The actual launch supplement carries selected request/digest/revision/outcomes
and staging-before-final-callback instructions; forthcoming invocation identity
is not session admission proof. CLI uses loopback session transport without
bearer fallback or automatic retry. This POST is browser-excluded and denied
natively before credential lookup/injection/forwarding. The private stage-only
comparison independently checks raw and normalized paths: literal query split,
one percent-decode with UTF8 replacement (malformed triplets and plus literal),
one final slash removal, existing prefix strip, exact family. Shared transport,
normalizer, H/G/r1/history and receipt validators retain their contracts.
This supersedes the historical reviewer-stage deferrals below; direct-submit,
B2 repository access, wider recovery/UI and Founder465
public two-template/revision smoke plus real Request changes AND Sign off remain
separate/root-owned. New behavior/proof is UNEXECUTED/FOUNDER-WAIVED139429/430;
integration SKIPPED24342, Python units RETIRED; automation is lint/static only.


**Matched artifact admission B1 — THR139seq480/477, ROOT TASK10693.**
A closed `artifact-review` definition with strict schema_version 4 compiles to
`workflow-artifact-contract@4`; exact pins are workflow-compiler@4,
workflow-validator@4 and operator-input@4. The template selects exactly one
accepted kind in output `{primitive:"immutable-artifact-revision",description,
accepted_kinds:["document"]}` or `["code-change"]`. Role names/kinds/counts,
outcomes, approval, return rules and submission timing remain finite template
data, independently bound to actual current principals. No name/content sniffing
or new product-design branch selects policy. Submission delivery retains the
literal current-author-handoff@1 declaration; B1 implements original-author r1.

Matched activation-request/receipt/authorization/binding/initial-draft-context@4
uses draft-artifact, stage-immutable-artifact, submit-immutable-artifact and the
existing collect-review/approve-planning-input/return-to-author semantics.
These grant preparation/review-input only. Context stores artifact_contract;
request requires repository_access; for documents it is null. Authorization,
binding and context require repository_binding null. A code definition may be
stored, but valid code activation returns409 workflow_repository_reader_unavailable
before instance/task/intent allocation. No code submission, repository reading,
access grant/capsule, execution, merge or delivered-code claim is implemented.

The document r1 chain uses author-handoff-command/request/proof/receipt@2.
Command adds artifact_kind document; proof adds repository_resolution null;
public core adds artifact_kind document. The source supplement gives the actual
@2 command and stable original key, staged before the genuine final callback.
The new submission ID preimage uses workflow-author-handoff@2. The review scope
is workflow-review-request@2 with the explicit artifact identity (org namespace,
instance and output slot), submission ID, strict revision1, document kind, exact
bytes digest and content_digest subject. It adds repository_resolution null.
Human-review-command/proof/receipt, review-stage-command, agent-review-proof,
review-receipt, review-request-receipt and review-join use matched @2 families.
B1 added no stage endpoint; seq475 staging is described above. Selected-principal/grant checks
precede private decode and repeat after waits/in the writer. Both authentic
receipt lanes, INTEGER/full callback and successful Boolean-quiescent terminal,
contributor independence, retained objections and collect-all rules remain.
No object I/O occurs in the writer or historical/cold readers.

Existing @1/@2/@3 historical definitions/contexts and @1 document handoff,
scope/brief/proof/receipt/join interpretations remain byte-exact. Unknown/mixed
families refuse; no relabel, backfill or automatic repin occurs. F/E/G/H DDL,
digests, H17 columns/constraints/revision1/unique intent and marker meanings are
unchanged. Publication requires pre-existing H. Any durable @4 template,
context or authorization already requires a compatible reader before the first
H row; no old-reader downgrade or migration claim follows. Compatible-reader
rollback must retain the new history; deleting/converting it is not authorized.

Prospective frozen reservations (non-null use remains held B2): repository_access
is `{action:"read-repository-artifact",repository_id,recipients}` with ordered
roles author then all reviewers. Binding is `{format:"workflow-repository-binding@1",
action:"read-repository-artifact",org_slug,repository_id,repo_key,origin_digest,
store_owner,authority:{namespace,generation,snapshot_digest},
recipients:[{role,kind,principal,team}],reader:"local-git-objects@1"}`.
Resolution is `{binding,binding_digest,presentation_bytes_base64,presentation_digest}`;
presentation is `{format:"workflow-repository-presentation@1",repository_id,
object_format,head,base,entries:[{path,before,after}]}` with each side null or
`{mode,blob_oid,bytes_base64}`. B1 accepts/stores only null binding/resolution;
these declarations are grammar reservations, never access or object-resolution
proof. Incompatible future shapes need a fresh version.

Existing routes/CLI/TS/OpenAPI carry matched unions, without a new route,
audience, native permission or shared transport change. B2 repository access
and bounded immutable-object reader, C r2/fresh signoffs/direct submission/owner
validity/reassignment/recovery/mounted UI remain held or separate. Founder465
public two-template/revision journeys and actual Founder UI Request changes
and Sign off remain root-owned UNOBSERVED; Phase2 ordered coding/QA/CI/merge is
separate. Behavioral/assertion/red-green/mutation/repetition/proof is UNEXECUTED /
FOUNDER-WAIVED139429/430, never PASS; integration SKIPPED24342 and Python units
RETIRED. Automated checks are lint/static only; no ad hoc E2E/live operation.
Fresh full independent review/scoped QA binds the final actual pushed head.


**Retained artifact foundation A — THR139seq480/477, ROOT TASK10693.** Pure frozen
`artifacts.ArtifactRevision` separates org/instance/stable output slot from a
stored submission ID, strict positive revision, document/code-change subject and
authenticated provenance. Current document integrity and both receipt subject
checks use it; legacy scope/brief wrappers preserve exact historical bytes,
IDs, digest preimages and error/admission ordering. No store/schema/graph import,
I/O or recursive validation enters the pure adapter. Closed canonical code-change
descriptors pin repository/object-format/full head/optional base and navigation,
but mount no repository reader or template admission. Product-design and the
one-human proposal share the document adapter; code shares the representation.
B2 repository resolution and C r2/fresh sign-offs/reassignment/recovery/UI,
Founder465 public/revision/real UI acceptance and Phase2 ordered coding/CI/merge
adapters remain outstanding. Seq493 approves seq475 staging as described above; H revision1/unique intent and @1/@2/@3 history remain unchanged.
Behavior/proof UNEXECUTED/FOUNDER-WAIVED139429/430; integration SKIPPED24342;
Python units RETIRED. Full independent review/scoped QA renew at final head.
Exact grammar: [Features And Invariants](docs/agent-guides/features-and-invariants.md).

**U3b(a/b/c/d) dependency-incomplete checkpoint (THR139 395/410/445/457/465).**
Internal F5 staging, genuine successful/quiescent agent receipts and authenticated
human receipts retain matched closed readers and terminal/reaper consumers.
Both receipt writers now collect ALL configured current template slots. Partial
responses preserve peers/findings. All responses with a declared objection close
the round while the instance remains reviewing and derive needs_revision/owner
revision action; no joined event or automatic r2. All approvals with current
approve-planning-input authority atomically close the round/instance and append
one immutable service:workflow_review_join event/replay, with full grant/pins and
actual receipt/result/assignment identities. Cold discovery reuses the existing
service; ordinary holds do not starve later work, corruption remains visible.
Historical joins grant no fresh approved use; current source/authority holds are
reported separately. No extra author signature or deferred direct-submit path
is introduced. Human public write now mounts the existing bearer writer; selected request GET
returns real pending/signed status with only recipient-safe findings/receipt fields.
Matching served models/OpenAPI/CLI/shared TS ship; stored scope/brief bytes remain.
Reviewer staging/native denial are described above; direct-submit stays deferred.
No shared transport/auth/session changes or new owner-read audience follow. Root-owned
two-template public smoke465 and later owner/revision/recovery/UI acceptance remain
dependencies. Retain OPEN/DRAFT/unaccepted/unmerged. Exact grammar:
[Features And Invariants](docs/agent-guides/features-and-invariants.md).
**Optional reasoning effort (THR298 seq15/46).** AgentDef frontmatter owns `reasoning_effort` beside `model`. Unset adds no CLI effort override and no workflow agent-projection key. Built-ins accept case-preserving fullmatch `[A-Za-z][A-Za-z0-9_-]{0,31}` through shared `model_effort_args` validation; historical catalog values are metadata, not a membership gate. UI uses plain text: blank on explicit Save maps to null/default, without trimming or truncation. Explicit wire/direct empty strings refuse; CLI clear retains its existing meaning. Custom adapters still require approved declared-value membership (at most16 lowercase identifiers); legacy/model-only effort controls remain disabled with saved values visible. Token acceptance proves syntax/transport only, not native model/account support. With explicit effort, OpenCode requires nonempty provider/model slash components (additional model path segments allowed), no whitespace/control/NUL, at most one # and a nonempty embedded variant byte-equal to effort; it composes one literal `-m provider/model#effort`. Unset preserves the original model transport. Pi recognizes only its existing finite thinking suffix set; arbitrary safe colon suffixes do not become native thinking syntax. Codex keeps one literal quoted config argv element. CLI changes clear omitted model/effort, unrelated writes preserve them, explicit null clears. Custom adapters retain input-v1 `model:null` unless an ordinary exact-artifact update declares input-v2. Bounded evidence does not verify dynamic manage_agent update/enrollment at runtime (source only), native models/accounts, full task/thread/dream/wake/schedule lifecycles, host/callback or custom contained-wrapper prelaunch. General integration including callback is SKIPPED THR243seq42; former Python units are RETIRED. No ad hoc Product E2E or protected mutation/repetition proof is authorized. See [current contract](docs/superpowers/specs/2026-10-11-agent-reasoning-effort.md) and the four agent guides. `CLAUDE.md` remains the raw symlink to this canonical file.

**G submission-schema migration (THR139 seq395; retained F/E→G path).**
The earlier fresh initializer selected G; U3a(a) now selects H under the H contract.
Existing F/E startup,
reopen and enable retain their installed layout; S2 remains available on E.
The explicit org-only operator command is
`python scripts/migrate_workflow_submission_schema.py --runtime-root <absolute-root> --org <slug> [--check]`.
Check returns migration-needed (3) for valid F/E, ready (0) for complete G,
refused (1) for invalid source/ownership, and parser errors return 2. Actual
migration atomically replaces only submissions/events and adds the approved
three tables/two explicit indexes. Event revisions come from an unambiguous
retained submission/round/event/replay closure; ambiguity refuses without
rewriting history. Every G database, including an empty one, needs a compatible
reader. The original F/E definitions and pristine-F downgrade contract remain.
The older draft script still upgrades F to E and reports a validated G as a
no-write G replay. Legacy authority comparison uses independent complete
F/E/G whole-database references; authority-v2 remains observed-only.

Compatible cold reopen preserves the complete durable schema, every table's
data and row identities, and the file set, modes and non-database bytes.
Ordinary authority recovery can commit and release an ephemeral lease, changing
SQLite physical pages; closed owners leave no lease or database sidecar residue.
Validator-only and unsupported-reader refusal checks separately retain exact
entire file-byte and mode preservation on a closed database.

An authorized upgrade requires the daemon to be stopped with its configured
home/registration observable and source owners/hosts reconciled. The command
reads bounded existing PID/port/registry evidence and reserves one SQLite
writer; it stops no process and provides no exclusion against an arbitrary
concurrent daemon start. Operator cooperation is a precondition. Active-origin
submissions retain NULL legacy result identity; separate authenticated operation
and INTEGER ordinary-result links preserve the actual result evidence.
U3a(b) internal submission/review/link producers are staged in the stacked draft.
Internal genuine receipts/collection/service joins are the dependency-incomplete
U3b checkpoint above. Public parity, later U3-U6 controls and independent operator
acceptance, including real Founder UI actions, remain pending. This implementation work does not authorize live migration,
enablement, deployment or a Phase1-completion claim.


**THR299 exact failed-continuation remedy.** Only the genuine ordinary
TASK10725 engineering_manager session in happyranch/engineering may write the
current-time authenticated FAILED10343 → existing10554 receipt, preserving
10565, older10319 and original FAILED dev10450/QA10329. The bounded verifier
adds this fully validated producer without changing old edges, failed-child
ownership,20-root/19-edge bounds, local failure or final-spawn/Committed enqueue
fences. One closed GET provides nonmutating advisory retry/preflight/historical
receipt modes; historical original-session evidence does not require live owner.
All relevant owner session_start rows are structurally validated before SID
selection; valid nonselected purposes/optional metadata retain original bytes.
The native connector denies the agent-only reconciliation POST before daemon
credentials/forwarding, while advisory GET and adjacent browser routes remain.
No task allocation, schema/auth/permission/queue change or startup reconciliation
occurs. Compatible-reader rollback is required. See
[the closed contract](docs/agent-guides/orchestrator-contracts.md#exact-failed-continuation-reconciliation-thr299)
and CLI/runtime guides. Founder299seq11 leaves unreachable synthetic positive
and dependent proof UNPROVEN; merge/install/real reconciliation remain separate,
owned by SAME TASK10725 before naming use.

HappyRanch is an org-agnostic runtime for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel; each organization is loaded from `<runtime>/orgs/<slug>/org/`.

**Current THR-259 workspace-cleanup safety contract.** The shared cleanup procedure accepts only a closed-schema, non-truncated host-job receipt binding the current task/session to the actual job, agent, stored command, interpreter, resolved cwd, timestamps, terminal result, complete output totals, and exact scanner coverage; it has no direct fallback. PR evidence is completely paginated and repeated, and open, closed-unmerged, duplicate, changing, conflicting, or malformed rows refuse. A removable containing worktree must be registered at its owning primary checkout's exact `.claude/worktrees/<TASK>` path on `task/<TASK>`. A cache must be positively Git-ignored, untracked, and absent from status before isolation. Before action and again at the action boundary, the literal candidate is completely walked without following symlinks; nested mounts, cross-device or foreign-owned entries, protected descendants, unreadable/capped/changing evidence, and identity drift refuse. The only external-link exception is a recorded `python`/`python3`/`python3.N` link directly under an owned literal `.venv/bin`, resolving to its configured uv store or `pyvenv.cfg` home outside every protected/workspace/candidate root; deletion unlinks and never follows it. Measurement includes the root inode, and success requires literal absence plus unchanged protected-path identities. Failed restoration after isolation is measured `isolation_anomaly`; failure after deletion starts is measured `removed_with_anomaly`. Both are exit-3 batch halts, never refusals, and account for original, isolated, and isolation-directory residuals without false zeroes. Batch resume accepts only a unique closed-schema terminal row exactly bound to the current manifest and argv; timeout, signal, malformed/mismatched output, unreceipted nonzero exit, runner exception, exit 3, or any unclassifiable outcome halts after journaling and before another candidate. Preparation compares explicit aware terminal instants with exact inclusive604800/86400-second floors and both action-time rechecks; naive/missing/malformed/changed times refuse. The packaged preparation helper freezes cap40/historical overlap exclusions without refill and distinguishes useful, honest complete empty/no-repo, and incomplete evidence. The packaged outer observer validates one supported request with omitted/relative cwd and actual remaining budget, never submits/retries, requires readable self host-init/unstacked-proc and protected metadata including workspace .codex/.aws without content reads, and adds no root-PID1 namespace-equality veto. Baseline failure admits no default packaged batch; batch and postcheck failures/drift retain both native causes and stay nonzero/incomplete. Empty leaves preserve native receipts without fabricated host baselines. Failed/unpublished authoritative lifecycle overrides local success/results; the genuine bound callback is the final session action. Known-excluded nonempty inventory requires verified same-bound preparation, original/copied inventory and manifest/history hashes, owner/native receipts and consistent excluded rows for no-action completion; state labels alone never suffice. Admitted result/pending facts do not bypass missing/failed inventory, baseline, postcheck or write evidence. Native batch argv/cwd/exit/raw is retained before fallible postwrites; after/sequence write failure stays unknown/nonzero with source/binding and both causes, using bounded captured cleanup_sequence_fallback JSON when sequence persistence fails. JSON writers flush/fsync/close one exclusive .NAME.pending sibling before no-clobber hard-link publication; pending siblings remain evidence and are never canonical inputs, with no unlink dependency or added directory-fsync/crash guarantee. Report --sequence accepts the exact captured failure fallback, retains native causes/source/binding/pending facts and blocks; missing canonical sequence also yields blocked intent. THR259seq506 repair grants no new cleanup allocation.

**Current THR-229 contract.** The current implementation contains the complete dual-text control/editor path, selector-family launch binding, strict completion admission, automatic pre-final/final/post-final continuation, startup/reaper recovery, authenticated publication/admission, and single-use next-result spend. The historical shipping proof (unit source retired by THR-291 seq40) ran a real ordinary child through queue/Dispatcher/run-step/provider launch on a fully historical-migrated database, retains `REQUEST_CHANGES` and the accepted raw-DDL inequality as diagnostics, wakes the same root, reports an arbitrary-reason escalation with a valid dual assessment through the real CLI, observes that root Pending before tagged dequeue, and spends/applies exactly one reserved next result. Every live manager with exactly one matching `teams.yaml` registration uses the same team-scoped surface, server-projected neutral v2 starter, selector/binding path, and downstream identity propagation; workers and stale, mismatched, or duplicate registrations fail closed before policy access. Existing persisted Engineering identities remain unchanged, while legacy compatibility is data-driven by `POLICY_BY_TEAM`. The UI retains immutable dual-text history but removes the two legacy read-only sections and eager requests. `web/scripts/screenshot-harness/shot-thr229-v2-policy.mjs` remains the owned browser receipt. No production policy is saved or activated by landing code. Older THR-229 checkpoint paragraphs below are historical implementation snapshots: their statements that later stages, the hook, recovery, or editor were dark/unimplemented apply only to that named checkpoint and are superseded by this current-state paragraph. Source merge is not rollout: compatible binaries must be deployed and every old manager launch/completion consumer drained before any future production activation; rollback is compatible-code-only. Exact-final-head CI, independent review/QA, guarded merge, deployment, and natural production continuation remain outstanding/unobserved, so the combined feature remains unaccepted.

**Current THR-229 v2 schema-observation correction (founder seq351).** The v2 decision path no longer performs a schema-integrity/reference comparison or schema recheck. Candidate and pin rows still store the real claim-time raw-schema digest, inventory digest, and object count as observed-only diagnostics; those values are never placeholders, compared, or rechecked, and structural differences cannot produce a v2 `schema_drift` refusal. Genuine observation failure retains the bounded `claim_failed` outcome. The historical fixture remains real migration-path test support, while the legacy v1 full-schema comparison (with S1 F/E references) and every non-schema v2 fence remain in force. The checkpoint C3a and issue #918 paragraphs below are historical implementation records superseded only for this removed gate; their recovery and idempotency behavior remains current.

**Current THR-279 v2 final-return diagnostic.** When the automatic v2 hook's final continuation call returns a bounded non-success status, the hook records that exact closed status and reason through the existing best-effort `authority_hook` `capture_failure` action before requesting the unchanged `final_commit_failed` refusal. A raised finalizer exception retains its distinct existing diagnostic. This changes no continuation decision, refusal code, audit action, task note, or identity semantics.

Keep this file short. It is loaded at the start of every Claude Code session. Detailed reference lives in `docs/agent-guides/`; read only the guide that matches the files you are touching.

**Task-scoped audit lookup (THR-278).** Generic `Database` initialization installs
the nonunique, nonpartial `idx_audit_log_task_id` on `audit_log(task_id)` for fresh
and existing databases. Literal task/config/thread/artifact scope values, audit
IDs, payload decoding and ascending-ID results remain unchanged. The index is
included in independent complete legacy v1 schema references and actual
observed-only v2 schema diagnostics. Failed-claim residue and malformed-attempt
startup fences retain their existing behavior. Source delivery, installed
schema, live startup timing and reviewer settings are separate evidence;
installation and compatible-code rollback guidance is in the runtime guide.

## Read When Touching

| Surface | Read |
| --- | --- |
| Project shape, architecture, runtime container paths | `docs/agent-guides/project-layout.md` |
| Settings, daemon startup, test modes, runtime config | `docs/agent-guides/runtime-and-configuration.md` |
| Executor behavior, workspace bootstrap, allow rules | `docs/agent-guides/agent-executors-and-permissions.md` |
| Orchestrator decisions, task state, agent files, teams, chains, failure-recovery, task/subtask terminology | `docs/agent-guides/orchestrator-contracts.md` |
| Web app, OpenAPI pinning, CLI behavior, agent callbacks | `docs/agent-guides/web-and-cli.md` |
| KB, learnings, artifacts, revisit, threads, jobs | `docs/agent-guides/features-and-invariants.md` |

`README.md` is for end users. `AGENTS.md` is the regular canonical repository-wide instruction file. `CLAUDE.md` is a raw relative compatibility symlink to `AGENTS.md`; maintain repository-wide agent instruction content in `AGENTS.md` only. For current behavior, prefer `docs/agent-guides/`, tests, OpenAPI snapshots, and implementation. Runtime rules live in implementation; retired unit references in historical
checkpoint paragraphs below are past evidence, not current executable coverage. Bundled agent instructions live in `runtime/skills/bundled/`; ordinary sessions discover eligible skills without a protocol-document index. `docs/superpowers/specs/` is append-only design history unless a spec is explicitly marked current in `docs/superpowers/specs/README.md`.

## Architecture: Canonical Skill Store + Workspace Symlinks (macOS + Linux)

Skill delivery uses a **canonical skill store** — hash-addressed packages outside executor workspaces — with **workspace symlinks** to exact approved package versions under both `.claude/skills` and `.agents/skills`. The legacy per-session wholesale copy is permanently removed. The executor runs under the same OS identity as the daemon — linked, validated relative skill links live under BOTH `.claude/skills` and `.agents/skills`; every user-facing and executor-facing guidance surface names both roots, never only the provider-selected root. Guidance is operational, not a technical security boundary.

- **Platform:** macOS (darwin) and Linux. Windows and unknown platforms explicitly fail closed.
- **Delivery model:** The executor and daemon share the same OS identity. There is NO OS-level isolation. A same-UID process may mutate, race validation, and affect active/overlapping sessions. Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal — do NOT claim immutable, protected, read-only, OS-enforced isolation, or automatic repair.
- **Integrity verification:** Before each launch the daemon validates every resolved package member's bytes against the ledger-declared SHA-256 hashes. Pre-launch and retry-time manifest/member-hash plus both-root link validation occurs at real Popen/run seams. On mismatch the daemon emits a durable visible integrity event and refuses the session before Popen/retry. A mismatched existing canonical package is NEVER automatically rebuilt, copied, replaced, or healed from same-UID local source. First-ever materialization of an absent package remains allowed; valid existing packages may be reused. Recovery is manual, operator-invoked only: `happyranch skills recover <slug> <version> <content_hash>` (accepts only the eligible current B2 version, validates its artifact provenance and all member SHA-256 hashes before deletion, and refuses already-valid targets). This command requires a preceding authoritative external re-sync/redeploy of release or custom artifacts. No automatic repair from same-UID local source. `set-executor` may repair links only after byte integrity passes — it never repairs bytes. Policy withdrawal and atomic link repair remain safe.
- **Session union:** All contexts (task, thread, wake, dream, schedule, bootstrap, executor-switch) use one fail-closed canonical verify/refuse boundary before launch. System-contract links are unioned across all ordinary session contexts so a later single-context launch never withdraws a valid link belonging to another context; release-managed and B2 custom-skill links remain policy-reconciled and withdrawable.
- **Retired System Assistant:** no feature routes, CLI, dock, bootstrap or config reader/writer. Preserve inert legacy files/links and historical records; keep the reserved `system_assistant` migration exclusion.
- **Custom-skill logical purge:** A founder may permanently tombstone only an already-retired current-v2 custom skill after exact-slug confirmation. Tombstones reserve ID/slug and permanently deny resolution, restore, mutation, and serving while retaining all versions, evidence, artifacts, canonical packages, and historical links. The default custom catalog excludes tombstones; explicit `view=removed` lists only tombstones, and direct detail receipts remain reachable. Recreating a tombstoned slug returns `409 slug_permanently_reserved`. Current eligibility rules are withdrawn by supersession, retaining their historical rows and making the preserved old resolver return `no_eligibility_policy`. At the unified canonical publication seam, one narrow per-org barrier spans the final authoritative tombstone/current-version re-read and both provider-root repairs; a purge committed during selection/build therefore excludes that stale spec from the returned set and both roots. Candidate construction and launch remain outside: there is no launch fence or running-session revocation, already published/running work is not recalled, no data is physically erased, and subsequent materialization remains fail-closed. Because `retired_at` remains set, downgrade alone cannot resurrect a tombstone; the deliberate pre-purge-binary limitation requires both operator downgrade and explicit restore of that exact skill, after which the policy-withdrawal latch still denies serving it.
- **Legacy fallback:** Permanently documented but cannot activate — link validation/repair, unsupported OS, or launch fail without catch-and-copy.
- **Residual risk:** Same-UID TOCTOU, active, and overlapping-session residual risk is accurately noted. Do not describe byte targets, local sources, ArtifactStore, or links as OS-immutable, ACL-protected, trusted, executor-only writable/unwritable, or automatically recovered.
- **Serving deployment** is independently verified after merge.

Detailed contracts: `docs/agent-guides/agent-executors-and-permissions.md` § "Canonical skill store + workspace symlinks", `docs/agent-guides/orchestrator-contracts.md`, `docs/agent-guides/agent-executors-and-permissions.md`.

## Essentials

- **Observation-only memory census (THR-091).** Real `OrgState` attaches a
  boot-local observer before workers. Actual `_run_agent` entry reserves intent
  independently of starts/impressions/reads; the same structured render freezes
  disabled/empty/nonempty expectations. Existing task-scoped audit rows record
  identity/binding/started-callback occurrences/own terminal plus seals. Loss,
  corruption, zero/pending/unknown population and failed initialization remain
  unavailable; application returns/exceptions and session ownership stay intact.
  Seals checkpoint counters without history reads and explicitly remain
  `census_not_reconciled`. Exhaustive read validation is bounded and compares
  closing semantic observer/database facts; movement or work exhaustion refuses.
  Internal census validity is never collection health or epoch authority.
  Seal-action `/audit` adds the closed optional serving-observation/loaded-identity
  view described in the corrective memory spec; it performs zero durable writes.
  G1 validates exact admitted manager/independent-QA results, prior finite
  delegation/command, real owned jobs/outputs and ROOT/CHILD operation evidence.
  Final acceptance appends an epoch; equivalent replay preserves its original
  boundary. Probe age <=48h applies at initial/reset final commit. Reports
  authenticate original admission and current full health without age-expiring
  that epoch. Installed acceptance and operational venues remain separate gates.
  See the memory section in the feature guide.

- **Workflow schema U1A foundation (THR-139).** U1A installs the
  unchanged inert version-1 foundation F from `OrgState.load`, after
  generic `Database` preflight/migrations and before teams, settings,
  membership validation, or orchestrator construction. Deliberate POST /orgs
  creation proves a fresh skeleton, then initializes complete H (F plus the
  reviewed E/G definitions/additions and exact THR139seq445 handoff table) in one
  transaction before attachment. Existing startup/reopen/enable never installs
  E: use `python scripts/migrate_workflow_draft_schema.py --runtime-root <absolute-root> --org <slug> [--check]`
  only with operator authorization. Check exits0 ready, 3 migration-needed,
  1 refusal (parser2); bounded contention/invalid targets refuse without partial
  installation. Generic
  `Database(...)` construction never installs it: machine-global
  `runtime-audit.db` and every adapter/executor audit caller remain untouched.
  One Database-owned synchronized `BEGIN IMMEDIATE` installs all workflow
  objects, version 1, the singleton `installed_legacy_only` generation 1 owned
  by `workflow_cutover_reconciler`, and its install event; interruption rolls
  back to zero workflow residue. Every reopen validates the full canonical
  table/column/default/PK/CHECK/UNIQUE/FK/index/trigger layout and the complete validated
  marker/event history (initial-only references need no org context; progressed
  histories require the actual org slug), refusing missing, extra, malformed, conflicting, newer, or
  wrong-owner state without repair. The legacy authority hook's release
  reference uses a private temporary generic database, applies this same
  canonical F, E, G or H reference after full layout/history/data validation, caches
  independently by layout, and hashes every non-null sqlite_master SQL object; it never filters
  workflow objects or installs them into a persistent generic/runtime-audit
  store. `WorkflowCutoverStore` now owns the existing seven-state one-way chain
  from installed_legacy_only through enable_requested, compatibility_verified,
  enabled, disable_requested, draining and drained. Founder-only requests use
  strict action/key/generation input; request/fence commits precede separate
  bounded reconciliation. Every authoritative reread validates the full layout
  and org-bound deterministic event chain; cold load advances only authentic
  committed requests only on ready E/G/H before teams/settings mutation. F keeps
  authentic history and actionable script guidance without recovery advancement.
  GET/preflight never advance.
  SQLite integrity/FKs and contradictory pre-enable work block verification;
  incomplete/nonterminal F5 closures block drain with actual ownership and
  deferred U2D/U4/U5 actions, never cancellation or task/outbox mutation.
  Downgrade is eligible only for pristine F initial history and zero workflow data.
  Every E requires a compatible reader, including empty marker-only E; the marker
  is structural metadata, while draft intents/events are work and their complete
  retained history/result closure is validated. Drain never settles draft work.
  Integrity hashes are cooperative coherence, not hostile same-UID proof.
  S2 now connects initial activation, draft dispatch and callback ownership as
  described below; operator UI and full Phase1 acceptance remain deferred. U1B adds only
  inert, immutable `product-design` template authoring/versioning in
  `runtime/workflows/templates.py`: current active managers publish only in
  their uniquely registered team's org namespace through verified task/session
  provenance, while Founder uses the existing bearer. One synchronized
  `BEGIN IMMEDIATE` revalidates authority and appends canonical JSON bytes,
  SHA-256 digest, gap-free version, CAS pointer and replay receipt in the
  existing U1A tables. Publication never activates, creates tasks/instances,
  dispatches, emits outbox/notification effects or changes cutover/authority
  state. U2A/U2B and the cutover prerequisite are shipped. S2 source adds the
  Founder-only exact-version initial admission and bounded document lifecycle;
  independent review/QA/CI and U3-U6/operator acceptance remain separate.

- **Initial workflow activation and draft ownership (THR139 S2).**
  `WorkflowActivationStore` is attached by `OrgState` and consumed by Founder
  POST/get/list `/workflows/activations`. Initial admission atomically allocates
  the actual task through the unchanged MAX allocator and freezes its root,
  exact template/version, request, authorization, binding/context and intent/event.
  Historical actor/org/key/request replay authenticates retained closure before
  mutable new-admission gates, returns the original receipt200 without queue
  effects, and projects current eligibility separately. New admission returns201
  only after commit; disabled/fenced admission creates no successful empty receipt.
  Profile discovery precedes async serialization, sorted selected profile leases,
  org publication ownership and the synchronized SQLite writer. No host/queue
  effect occurs under durable ownership. Draft/F5/malformed closures are routed
  before legacy startup/run_step/completion/cancellation effects. Startup and
  periodic sweeps rediscover the same authenticated queued intent after lost
  notifications or author-capacity refusal. Periodic discovery checks current
  authority/capacity and deduplicates enqueue only after all leases release;
  claim and prelaunch still arbitrate, without an immediate retry loop. A real session
  is not launch evidence: possible launch is durably reserved, running needs a
  genuine bound handle, and completion needs the exact accepted INTEGER result
  plus finalized host quiescence. Missing acknowledgment/callback/quiescence stays
  workflow-owned uncertain; PID/TTL absence cannot settle it. Cancellation fences
  before containment. The author, including a manager, has only bounded document
  responsibility; no generic manager decision/delegation/fanout is consumed.
  CLI `workflows activate --org ... --from-file /absolute/request.json` and
  `workflows activations list/show` use the existing Founder client; parser2,
  domain/transport1, valid receipt including pending0. This adds no DDL or
  migration or reassignment/reactivation API. S2 alone grants no U3 submission/approval;
  staged U3a(b) ownership is documented separately below.
  Source is not deployment or Phase1/operator acceptance.

- **Task-scratch reclamation contract (THR-195 B1).** `runtime/daemon/task_scratch_reclamation.py` accepts only immutable finalized ledger rows for valid canonical manifested `TASK-*` roots. Its caller-constructible `*Assertions` values are explicitly untrusted shapes, have no permissive defaults, and reject missing, malformed, stale-boot, truncated, ambiguous, unsupported-platform, recovery/job/live-reference, unavailable, or internally inconsistent values; B1 does not establish their provenance or independently validate lifecycle/liveness/current-boot authority, whose producers are deferred to B2/B3. The private `collect_revalidate_seal_consume_disposable` seam retains the typed values from each existing bounded E/C admission, compares liveness/session/process and complete coverage projections (including bucket classification/accounting and dominance) across E1..E4/C1..C3 while excluding collection timestamps, and binds final C3's canonical workspace/root/manifest/census fields to its successfully sealed stack-local row. It refuses malformed/private-identity/boot-mismatched or exhausted bounded observations before execution, and a partial executor failure claims zero while preserving the remainder for a fresh refusal. The sole production caller is the bounded pre-agent `run_step` hook. It is disabled by default through the strict boolean `workspace_cleanup.reclamation_actions_enabled` (default `false`), and disabling affects later admissions only — it cannot revoke an already admitted consumer call. It acts only on a third-or-later cleanup ordinal whose scheduler-created preclaim owner is assigned to a registered in-memory `TeamsRegistry` agent and reconciles to this invocation's initial successful `0 -> 1` claim (the first two runs stay report-only), applies one shared one-second deadline and at most 23 read/load admissions with no refill or recovery, and makes at most five best-effort calls to the unchanged consumer. Each attempt records the owner `workspace_cleanup_reclamation_attempt` audit before the prompt carries the known facts into the ordinary completion summary; the transported remainder is exactly the returned `after` accounting or `null`; scheduler triggering/order remains unchanged. It adds no writer fence or future-writer/same-UID guarantee. Sealing also re-derives the canonical root and applies the 60-second newest-mtime floor. Execution hard-skips Git/worktree/bare-repository ancestor or descendant evidence and cross-device ambiguity, uses fd-relative no-follow pathname removal with verified parent/root identity, exact allocated-byte/inode accounting, and complete protected manifest/lock/parent/directory-entry/sibling postconditions. Detected pre-action identity mismatches fail the row with zero reclaimed claims. Portable POSIX unlink/rmdir is not inode-bound: the threat contract deliberately excludes a hostile same-UID replacement in the final identity-check-to-pathname-syscall window, and does not promise that replacement survives. The separate report-only coordinator remains report-only after teardown and through the existing daily trigger; deployment, legacy-backlog eligibility, and activation beyond the explicitly enabled hook remain absent. Unmanifested `.happyranch/tmp`, `.tmp`, `.task-cache`, `.t`, shared `/tmp`, and pre-contract roots remain ineligible.

- **Report-only task-scratch evidence (THR-195 B2a).** `runtime/orchestrator/task_scratch_evidence.py` is consumed by the bounded report-only coordinator after task teardown and from the existing daily trigger. Task-scoped `task_scratch_report` audit observations record provenance, freshness, retention reasons and zero actual reclamation; no engine or deletion call follows. Its collector takes a finite, shared-deadline, fail-closed observation from full durable task/revisit/job/result records, advisory sessions, and Linux boot/PID root/cwd/open-fd reads; it checks admission before each bounded read, source open, and iterator advance, while a read already in progress remains nonpreemptible. Unavailable, partial, capped, stale, or identity-invalid measurements are not zeros; independently complete measurements (including zero) remain truthful when lifecycle, session, or durable authority alone rejects eligibility. Terminal rows, retained terminal results, and cleared trackers are not process-exit proof; B2b must recollect immediately before any action, and B3 remains coverage authority. It imports neither the engine nor `execute_ledger`. The separate database selection helper is read-only; its sole caller is the bounded pre-agent `run_step` hook, which supplies its initial `0 -> 1` claim context and per-read admissions. Each bounded SQL/decoding observation refuses as `None` when unavailable or malformed, and the helper still performs no config/consumer/audit work or grants action authority.

- **Report-only task-scratch coverage (THR-195 B3a).** `runtime/orchestrator/task_scratch_coverage.py` is consumed by the report-only coordinator with literal candidate identity bracketing, boot/time correlation and fresh recollection of any supplied coverage. It makes two bounded boot-bound observations of a fixed workspace partition, counts allocated bytes and entries (including hardlinks per observed entry), and describes dominant canonical or residual consumers. Every stat, open, dependent buffered read, directory read, and iterator advance is separately admitted against one deadline and finite shared bounds; manifest reads are limited by remaining actual-byte allowance and boot bytes do not consume that allowance. Literal no-follow validation rejects present symlink/non-directory optional, candidate-root, and manifest-parent paths, while absent optional parents are not invented as residual partitions. Incomplete candidate scans remain unknown and fail closed; observed repository/special entries remain disqualifying. Unavailable, changed, capped, zero, or partial observations are never ready. This is neither a permit, a free-space forecast, nor writer exclusion; B2b must recollect at action time.

- **Team escalation policy editor and activation (THR-181 S5/S6b/S7).** Every live manager with exactly one matching `teams.yaml` registration receives a compact manager-only entry/status card. Its dedicated org/agent-scoped policy page receives the server-owned neutral dual-text starter plus any registered legacy bootstrap template from the authenticated team projection; TypeScript carries no second text or clause authority. The dedicated page shows bounded secret-free immutable dual-text history. Legacy history and manager self-evaluation outcome APIs/data remain available, but their two read-only UI sections and eager requests are removed; worker, stale, mismatched, and duplicate-registration DOM/query/payload surfaces remain absent. Activation is an explicit founder-authorized action subject to ordinary authenticated route validation, exact live AgentDef/unique-registration resolution, team-scoped request and immutable release linkage, create-then-CAS, idempotency, audit, and monotonic epoch fences. Rollback reactivates an older immutable release as a new epoch. Code landing, redeploy, and a separately authorized production-policy activation are distinct events; this code creates or activates no production policy.

- **Active team escalation policy (THR-181 S4).** Only the authenticated current immutable release is injected, under the server-reserved `## [RESERVED] Active Team Escalation Policy` section, into task, fresh/resumed thread, wake, dream, and schedule prompts for a live manager with exactly one matching team registration. Worker, stale, mismatched, and duplicate-registration prompts remain byte-absent. Every untrusted rendered input rejects the reserved header and begin/end markers. No active release preserves ordinary launches. Launch durably records the exact team and rendered release/activation identity; every downstream v2 identity uses that immutable binding rather than current registry state, client input, or a constant. The authority hook reads only that session binding, never mutable current activation. DB-policy authority candidates atomically pin that identity and evaluator provider/executor plus the candidate's prompt/model identities, and the pin/release is re-authenticated before evaluation recording and continuation commit. Legacy static candidates remain unpinned and are never backfilled.

- **Versioned dual-text policy callback admission (THR-229 checkpoint C2, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR as C1 and is not part of the running daemon until a later guarded merge. The shipping `report-completion --from-file` transport now decodes a present v2 `manager_self_evaluation` with the repository's strict decoder before the ordinary lossy parse is trusted, preserving omission versus explicit `null` and rejecting duplicate JSON members, UTF-16/32, a BOM, non-finite numbers and invalid UTF-8 without durable writes or raw-content echo; non-v2 legacy payloads keep their prior permissive behavior. The callback route dispatches by the AUTHENTICATED launch binding (never the live selector or a claimed wire family): a `v2` binding validates the strict `AuthorityPolicyV2ManagerSelfEvaluation`, authenticates every identity field against the immutable C1 binding (root/team/session/release/digest/revision/activation/selector epoch/contract/provider/executor/model) and admits; a `db_release` binding keeps the unchanged v1 path; missing/static bindings and mixed or unsupported family evidence refuse without a v1/static fallback. The sanitized assessment (or a bounded digest-only fail-closed diagnostic for missing/null/malformed/mismatched evidence) is persisted through the existing result decision carrier and preserved for the future consumer. One synchronized callback admission transaction inserts the immutable result, the new `authority_policy_v2_attempts` row (deterministic `APV2R-` identity over `(root, manager, session, result)`; `stage=admitted`, `finalization_state=unfinalized`, daemon-process `origin_boot_id`, winning-insert `owner_attempt_id`) and the `authority_policy_v2_result_stage` `admitted` audit, atomically with any applicable callback_accepted receipt transition; any failure rolls back every new result/attempt/audit/receipt change. An exact transport retry is read-only success that preserves counts/IDs/owner (both after the tracker clears and while it is still active), while a changed assessment digest refuses. C2b authenticates the complete admitted evidence at both retry seams and the typed reads: the transaction retry re-reads the raw attempt columns through the same canonical column/preimage validation as the typed readers, requires exactly one closed `authority_policy_v2_result_stage` `admitted`/`unfinalized` audit whose origin/owner and every closed reference match the immutable attempt identity, and compares the complete normalized persisted completion projection (decision, summary, status, confidence, verdict, risks, output path, wait IDs and local-CI evidence) with semantic JSON equality derived from the ordinary route-to-insert projection; a missing, deleted, mutated, duplicated or mismatched attempt/audit, or any changed completion field (after the tracker cleared as well as while it is active), refuses with no allocation, repair or second result/attempt/audit. Server-owned timestamps/boot identity are excluded, and the v1 path is unchanged. C2 implements admission and authenticated reads only — there is no candidate/pin/evaluation/envelope/notification/dispatch table, no early continuation entry point, and the current authority hook still fail-closes unsupported v2. The accepted C1 launch-binding paragraph below remains in force. Staged debt still owed (NOT implemented here): the candidate/evaluation/continuation consumers, the corrected adverse/partial/raw-DDL diagnostics and recovery/generation admission fences, the exact-stage refuse/finalize/receipt-settlement/publish/admit/spend transitions, and the complete editable-pair editor/browser/activation/readback path. The accepted R3 real owned-RuntimeDir/isolated-org subprocess shipping fixture has since landed at `tests/test_authority_v2_shipping.py`: it bootstraps a fully owned runtime containing exactly `engineering_manager`/`dev_agent`, runs the real app/uvicorn lifespan on a fixture loopback port/token, saves+activates the v2 pair through the real isolated API, creates a real root and enqueues it through `runtime.daemon.runner.enqueue_task`, and drives the real `TaskQueue`/`Dispatcher.run_step` -> `Orchestrator.run_step`/`run_step_impl` -> atomic claim -> `_run_agent` with ONLY `_launch_agent_with_scratch` doubled at the external launch boundary. The completion is the actual shipping subprocess CLI; the fixture asserts the durable sanitized assessment, exactly one result/attempt/admitted audit, an exact read-only retry, a changed-decision refusal and (in a separate disposable case) a missing-admitted-audit refusal, then releases the held launch so the real run-step consumer handles the durable result. It carries the current documented fail-closed v2 outcome (ESCALATE, no continuation) because the v2 continuation consumer is not implemented; it is reusable for later continuation cases but does not fabricate their Pending/enqueue outcome. Until those consumers land and merge, the dual-text feature remains unaccepted.

- **v2 constraint-sensitive schema-integrity prerequisite (THR-229 checkpoint C3a, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. `runtime/orchestrator/authority.py` now exposes the accepted SEPARATE, read-only, callable v2 schema-integrity seam — `capture_authority_policy_v2_schema_integrity(db)` and `recheck_authority_policy_v2_schema_integrity(evidence, db)`, returning the narrow typed evidence `AuthorityPolicyV2SchemaIntegrity` from `runtime/models.py`. Its reference is constructed independently of the candidate database: fresh current source plus ONLY the two accepted exact migrated `threads`/`thread_messages` ordered-layout substitutions and their index-cid consequences. The oracle compares the complete non-internal object inventory — full table SQL including CHECK/UNIQUE/FK expressions, ordered `table_xinfo`, `foreign_key_list`, complete `index_xinfo` (expression sentinels/collation/key flags/cid), `index_list` origin/unique/partial, explicit index SQL/predicate and full trigger/view SQL — retaining autoindex constraint metadata and ignoring only rootpage/allocator and SQLite's reserved internal `sqlite_`-prefixed objects — matched as that exact literal, case-insensitive prefix rather than a SQL `LIKE 'sqlite_%'` pattern, so a legal user object that merely resembles the internal namespace, e.g. `sqliteXunreviewed`, is still inventoried and refused as unexpected). Every candidate read and the frozen raw digest run inside ONE `Database.coherent_read_view()` — the shared-connection lock is held across the whole capture and one SQLite read snapshot is pinned, so an independent connection's commit can neither split the inventory from the digest nor be authenticated by a stale inventory; it can only make the subsequent recheck refuse. It requires `PRAGMA integrity_check` exactly `ok` and zero `foreign_key_check` violations, then captures the validated candidate's ACTUAL raw `sqlite_master` DDL digest. A recheck denies ANY later raw-digest drift, including a switch to a different accepted layout; a failed/unavailable capture can never become a successful recheck, and unknown layouts/read errors fail closed with bounded machine-readable diagnostics (never raw schema/data prose). This is integrity EVIDENCE only — not policy authority, not a policy-clause match and not a grant; it never repairs the candidate. The legacy `_release_schema_digest`/`_live_schema_digest`/`_server_evidence`/`_server_fact_clause`/`_during_attempt_drift_clause` v1 behavior and all its callers are unchanged, and the seam is NOT yet wired into the authority hook. The reusable checked-in full historical schema fixture `tests/fixtures/authority_v2_historical_schema.json` (generated in isolated subprocesses from `f39b4934611ca13ab7d8b7fa2d7be983a4bfb7a5` with recorded provenance/source hashes, reconstruction support in `tests/authority_v2_historical_schema.py`) rebuilds the whole old schema — including nullable-without-default historical `task_results.output_summary`/`confidence_score` — and opens it through the actual current `Database` migration path; `tests/test_authority_v2_schema_integrity.py` drives the real seam over pristine and mutated models, and the R3 shipping venue now also runs over that migrated DB with the single launch hold installed BEFORE enqueue. Staged later consumers still owed (NOT implemented here): the persisted candidate/pin/evaluation/continuation consumer, the corrected adverse/partial/raw-DDL diagnostics, the recovery/generation admission fences and the editable-pair editor/browser path. The authority hook still fail-closes an unsupported v2 consumer outcome (ESCALATE, no continuation) and the dual-text feature remains unaccepted.

- **Durable v2 candidate/pin claim and claim-audit stages (THR-229 checkpoint C3b, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. `runtime/infrastructure/database.py` adds exactly three additive tables — `authority_policy_v2_candidates` (K), `authority_policy_v2_pins` (P, identity equals the candidate identity) and `authority_policy_v2_candidate_audit` (a1 closed event, real candidate FK, append-only immutable payload) — with their indexes/triggers, and completes this PR's own new `authority_policy_v2_attempts` stage CHECK to `admitted|claimed|claim_audited` (no released column altered/dropped/reinterpreted; `audit_log` action scope unchanged). The candidate identity is exactly the frozen R2 claim-key preimage (`APV2C-` + the claim-key digest; `claim_key` uniqueness prevents a second candidate for the same causal tuple). `Database.claim_authority_policy_v2_candidate` refuses transaction nesting with the closed code `transaction_owned` BEFORE it would `BEGIN`, `ROLLBACK` or invalidate the live owner whenever the caller already owns a transaction — the caller's transaction and its pending work remain untouched and the two stage commits stay independent (no merged transaction, no silent savepoint, no changed R4 durability) — and otherwise runs ONE synchronized `BEGIN IMMEDIATE` that re-reads and authenticates the actual immutable admitted result: exact attempt/root/team/manager/session/result plus `origin_boot_id`/`owner_attempt_id`, `unfinalized`/`admitted` state, the single authenticated `admitted` audit, the actual task (`in_progress`, null `block_kind`, null `cancelled_at`, current owner/session), the causal result row/task/agent/session AND its normalized body (the persisted `_manager_self_evaluation` carrier must re-hash to the admitted `assessment_digest`; CRD is only the row-identity digest), the immutable launch binding, the pinned release/activation/selector identity through its own pinned epoch (a later legitimate activation never replaces or invalidates an older pin; corrupt/mixed pinned history refuses with no fallback to today's policy), and the resolved provider/executor/model. Claim also freezes the ACTUAL claim-time schema evidence (raw/inventory digest and object count) and bounded read-only permission-surface evidence (a digest read through the narrowly scoped server-side reader bound on the Database as `reader(agent)`, never a caller-supplied allow/deny boolean or precomputed digest) onto BOTH K and P; an unbound/read-failed/malformed read fails closed with no sentinel digest, and these evidence columns are not claim-preimage inputs. The applicable mechanical eligibility predicates (revisit lineage, active chain/fanout, blocked job, successor root, revise-budget ceiling) are re-derived from the persisted task row — no caller boolean and no `_server_fact_clause` adverse-review/partial-work/raw-DDL clause is a v2 veto or a phrase/clause unlock. On success it inserts K+P and advances J to `claimed`, preserving R and a0 and NOT appending a1. A separate second transaction then RE-AUTHENTICATES the complete evidence rather than trusting K/P — the causal result row/body, immutable binding, authenticated pinned release/activation/selector prefix, the full candidate/pin/attempt/release/binding joins (provider/executor/model/version/digest/boot/owner), the prior a0 and the current task ownership/cancellation — and rechecks the ORIGINAL frozen claim-time schema/permission evidence under its owned transaction (no recapture/rebaseline); it inserts exactly one candidate claim event plus the required `claim_audited` result-stage evidence and advances J to `claim_audited` atomically. A failed claim leaves J admitted/a0 and no K/P; a failed claim-audit preserves the claimed K/P with no a1. An owned-stage failure, a genuine cancellation/replacement, or a schema/permission-drift refusal by the authentic uninterrupted owner poisons the winning token so a later retry cannot become a fresh authority; an unauthorized/stale/duplicate contender (wrong boot, wrong owner, wrong tuple, second connection, duplicate call, transaction-nesting refusal) is classified before liveness and never poisons the winner. Refusals return only a bounded `AuthorityPolicyV2StageOutcome` (closed refusal code); this checkpoint performs NO refusal housekeeping and NO evaluation/consume/envelope/finalization, and does not mutate tasks into Pending/Escalated. Exact transport retries after legitimate progression stay read-only success (the admission-audit authentication is now scoped to exactly one authentic `admitted` event inside its own stage, so later stage events never invalidate a0), while a changed client payload still refuses. The later refusal-housekeeping consumer (future THR-229 unit) owns any durable refusal/settlement; the shipping authority hook remains fail-closed (ESCALATE) and the dual-text feature remains unaccepted.

- **Durable v2 assessment evaluation and single consumption stages (THR-229 checkpoint C3c, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. `runtime/infrastructure/database.py` adds exactly one approved additive table, `authority_policy_v2_evaluations` (V; `evaluation_id` equals the candidate ID, `UNIQUE(candidate_id)`, immutable no-update/no-delete triggers), extends this PR's own new `authority_policy_v2_attempts` stage CHECK and transitions to `admitted|claimed|claim_audited|evaluated|evaluation_audited|consumed|consumed_audited`, extends the candidate-audit closed event set to `claimed|evaluated|consumed`, and adds a forward-only candidate lifecycle (`lifecycle_stage` `created|evaluated|consumed`). The C3b blanket candidate-no-update trigger becomes a precise guard: every identity and frozen-evidence column (including the claim-key, attempt/result/binding, pinned release/activation/selector, resolved provider/executor/model, owner/boot and frozen schema/permission digests) stays immutable and only `created -> evaluated -> consumed` may advance (no skipped, repeated or reversed transition). Exactly four new callable, Database-owned, independently committed methods implement the accepted R4 pre-final stages, each refusing caller transaction nesting with `transaction_owned` BEFORE it would `BEGIN`/`ROLLBACK`/invalidate the owner, and each re-authenticating the complete prior evidence rather than trusting the persisted rows: (1) `evaluate_authority_policy_v2_candidate` re-reads the persisted sanitized `_manager_self_evaluation` carrier, validates its full bound identity (root/session/release/policy/activation/contract/resolved provider/executor/model) and both assessments, derives the clause-free outcome ONCE through the existing pure `derive_authority_policy_v2_assessment_outcome` (a missing/null/malformed/mismatched diagnostic carrier is retained honestly with the fail-closed `invalid` outcome and its closed code, never invented into a valid assessment), inserts ONE immutable V, and advances K `created -> evaluated` with J `claim_audited -> evaluated` atomically; task and recovery receipt are unchanged and no envelope/notification/dispatch exists. (2) `audit_authority_policy_v2_candidate_evaluation` re-authenticates the complete prior evidence INCLUDING the stored V and a0+a1, appends exactly one candidate `evaluated` event (a2) plus the `evaluation_audited` result-stage evidence, and advances J — never re-deriving the outcome to authenticate V. (3) `consume_authority_policy_v2_candidate` re-authenticates a0+a1+a2 and the stored V, requires the evaluated candidate and J `evaluation_audited`, and CASes K/J to `consumed` exactly once with no second model call and no repeated derivation; it mints no authority and does not change the task. (4) `audit_authority_policy_v2_candidate_consumption` re-authenticates the consumed K/P/V and a0+a1+a2, appends exactly one candidate `consumed` event (a3) plus the `consumed_audited` result-stage evidence, and advances J in one transaction. Failed stages leave the exact prior residue (`interrupted_pre_final`-style): a failed evaluation leaves K `created`/V absent; a failed evaluation audit preserves V and evaluated K; a failed consumption leaves the evaluated candidate; a failed consumed audit preserves the consumed K and V. An owned-stage failure, cancellation/replacement, evidence drift or a genuine wrong owner poisons only the authentic uninterrupted owner's token; an unauthorized/stale/duplicate contender, a transaction-nesting refusal and a second-connection loser are classified before liveness and never poison a still-valid winner. The ORIGINAL frozen claim-time schema/permission evidence is rechecked under the owned transaction (no recapture/rebaseline), and the method is referenced by the shared candidate-evidence helper so the earlier claim-audit boundary keeps identical behavior. The C3c correction then (a) authenticates BOTH halves of every required prior stage audit at each public evaluation / evaluation-audit / consume / consumed-audit boundary — exactly one closed `authority_policy_v2_result_stage` event per required stage (`admitted`, `claim_audited`, and `evaluation_audited` from consumption onward) with the exact closed key set, the bound identity, the candidate ID for later stages and the required `unfinalized` finalization marker, alongside its sibling candidate-audit event — never requiring the audit created by the transaction currently running, keeping `a0` replay scoped to `admitted`, and bounded-refusing missing/duplicated/mutated/foreign/extra-key/malformed events with no V/audit/advancement and no repair-by-reinsertion; (b) inspects the actual persisted manager `decision_json.action`, separately from the dual assessment, and requires the accepted root escalation `escalate`, so a missing/non-escalate or between-stage-drifted decision cannot acquire the continuation path (R2 content-ID preimages and CRD are unchanged, no prose/sentinel detector is introduced, and ordinary manager validation plus all real protected-action gates remain); and (c) re-derives the retained current mechanical eligibility (root-only, ownership/cancellation, chain/fanout/blocked-job, revisit/successor and the current server-owned `max_revise_rounds` ceiling against the persisted `tasks.revision_count`) at EVERY stage boundary, threading the scalar ceiling from the existing `authority_policy_v2_claim_eligibility(orch)` seam rather than an invented allow boolean or a value silently lost after claim. The later THR-229 refusal/reaper/recovery/finalize/settle/publish/admit/spend consumer and the complete editable-pair editor/browser path remain UNIMPLEMENTED; the shipping authority hook still fail-closes an unsupported v2 consumer outcome (ESCALATE, no continuation) and the dual-text feature remains unaccepted.

- **Durable v2 pre-final refusal and exact recovery-receipt housekeeping (THR-229 checkpoint C3d1, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. `runtime/models.py` adds the closed terminal refusal vocabulary — `AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES` (`interrupted_pre_final`, `claim_failed`, `claim_audit_missing`, `evaluation_failed`, `evaluation_audit_missing`, `consume_failed`, `consume_audit_missing`, `final_commit_failed`, `identity_mismatch`, `owner_lost`, `cancelled`, `decision_dispatch_interrupted`), the `refused` candidate-audit event, the `refused` result-stage marker, `AUTHORITY_POLICY_V2_HOUSEKEEPING_PENDING_CODE` (`authority_v2_housekeeping_pending`), `AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION`, and the bounded types `AuthorityPolicyV2HousekeepingTarget` / `AuthorityPolicyV2HousekeepingOutcome`. `AuthorityPolicyV2Attempt` may finalize as `refused`/`owner_lost` with a closed `refusal_code` while retaining its greatest committed `stage`, exact immutable identities and canonical evidence; an additive unreleased trigger (`authority_policy_v2_attempts_finalization_guard`) makes a finalized attempt terminal so it can never reopen, advance or rewrite its refusal evidence, and the candidate-audit CHECK/closed event set gains `refused`. All refusal mutations happen in exactly ONE `Database.finalize_authority_policy_v2_attempt_refusal` synchronized `BEGIN IMMEDIATE` transaction over exact J/R, the current task and the optional exact recovery receipt Q; it refuses caller transaction nesting with `transaction_owned` BEFORE it would `BEGIN`/`ROLLBACK`/invalidate the live owner. It authenticates only immutable OWNERSHIP evidence (attempt/result/binding identity and the greatest durable stage) and never the failed CONTINUATION evidence whose absence caused the refusal — a missing stage audit or corrupted assessment is not required and is never reconstructed. For the still-current causal owner (`in_progress`, null `block_kind`, not cancelled) it writes the closed refusal result-stage event, a candidate `refused` event if K exists, the normal `escalation` audit and the bounded `completion_report` refusal audit, CASes the task to `escalated` and J to `refused`, and settles a `callback_accepted` Q to `callback_consumed` in the SAME transaction only when it exactly matches task/agent/recovery session/accepted result/session; ordinary callbacks keep Q absent. A cancelled/terminal/replaced owner preserves the winning task row exactly and records the old attempt `owner_lost`/`cancellation` disposition and audits, settling only an exact obsolete matching Q and never a replacement receipt; an unrelated/mismatched receipt is never labelled ordinary/absent. No successor/new root, envelope, notification, dispatch generation or queue effect is created and raw model prose never enters a bounded refusal diagnostic. Live-owner safety binds the trusted current daemon-process identity through `Database.bind_authority_policy_v2_process_boot_id` (the same per-daemon `authority_v2_origin_boot_id` recorded on admitted attempts — never a caller allow boolean): only the authentic in-memory owner token, an old-boot attempt (`origin_boot_id` differs from the bound process identity), or a server-written durable failed-stage obligation may finalize; a same-boot attempt with no token (e.g. a second Database instance) cannot prove the uninterrupted winner is dead and returns bounded `housekeeping_pending` without touching task/Q/J. Owned-stage failures record that durable obligation (best-effort, closed code only) BEFORE poisoning the process-local token, and no process-local liveness is reconstructed on reopen. A failed refusal transaction is refusal-only: it rolls the WHOLE terminal transaction back, preserves the original exception truthfully, and — only when the caller presented the exact authentic live-owner token — poisons that token (prohibiting any later claim/evaluate/consume/audit advancement and final mint) and records the same bounded best-effort durable obligation; a process-local failure-ownership marker keeps safely attributable housekeeping retry possible even when the durable diagnostic could not be written, and it is never reconstructed from durable UUIDs. Malformed/foreign/nested/duplicate contenders are classified before poisoning and never poison or finalize a valid winner. Already-finalized attempts return a read-only `already_refused` exact replay only when the complete identity-scoped terminal evidence set authenticates as ONE closed set — the exact causal result/root/manager/session and immutable launch binding authenticated FIRST (never the failed continuation body/assessment), the refusal result-stage event, exactly one bounded completion audit (a duplicate/conflicting refusal code is not filtered out of the uniqueness check), the normal escalation audit for the still-current-owner `refused` outcome (never required for the `owner_lost` outcome that preserves a different winning task), and the candidate `refused` audit when K exists; deleted, duplicated, mutated, conflicting, foreign or extra-key terminal evidence is refused and never repaired, and no second audit is minted. Receipt identity comes from a real Q: an explicit `recovery_session_id` with no actual matching Q fails closed with the bounded `receipt_missing` pending code and mints no recovery-shaped completion evidence, a genuine ordinary absence stays ordinary, and when the stored completion carries the exact recovery fields the replay additionally requires the exact `callback_consumed` Q with the exact task/agent/recovery-session/result/result-session tuple (a deleted/transition-only/replaced Q is missing evidence, never "ordinary"). Callable read-only discovery (`list_authority_policy_v2_unfinalized_attempts`, `get_authority_policy_v2_housekeeping_target`) surfaces unfinalized attempts including a failed claim that created no K, and the durable J/R or recorded obligation remains discoverable after a refusal transaction failure; the failure path returns/logs `authority_v2_housekeeping_pending` with the attempt ID and honestly preserves task/Q/J, while an unwritable store promises no new durable diagnostic. `runtime/orchestrator/authority_policy_store.py` exposes thin forwards (`bind_v2_process_boot_id`, `finalize_v2_attempt_refusal`, `list_v2_unfinalized_attempts`, `get_v2_housekeeping_target`) that never commit. The shipping authority hook remains fail-closed: the later publication/admission/spend transitions, the startup/reaper/run-step automatic discovery wiring, and the complete editable-pair editor/browser path remain UNIMPLEMENTED and the dual-text feature remains unaccepted.

- **Atomic continuation finalization and exact receipt settlement (THR-229 checkpoint C3d2, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. It adds exactly the three remaining approved additive tables — `authority_policy_v2_continue_envelopes` (E), `authority_policy_v2_recovery_notifications` (N) and `authority_policy_v2_root_dispatch` (D) — with their immutable identities, uniqueness, indexes and lifecycle checks/triggers. E identity is exactly `APV2E-` + H({"candidate_id":C,"kind":"continue_envelope"}), is unique by candidate and repeats/authenticates the complete pinned tuple (candidate/claim_key/attempt/result/binding/contract/release/activation/selector/provider/executor/model/causal-result/owner/boot plus the persisted V `continue_applies` outcome); it is inserted ACTIVE and only the forward-only active -> consumed lifecycle may advance (the later spend unit owns that transition). N identity is exactly `APV2N-` + H({"envelope_id":EN,"kind":"continuation_notification"}) — the immutable continuation generation token G — unique by envelope and by exact causal tuple, carrying the closed `needed|publishing|published|admitted|settled|invalidated` lifecycle, publication attempt/boot/lease and nullable reserved next session. D is keyed by root, names one generation G with `pending|admitted|retired`, preserves the expected causal owner/session and admits only one non-retired generation. ONE `Database.finalize_authority_policy_v2_continuation` synchronized `BEGIN IMMEDIATE` transaction requires J unfinalized/`consumed_audited`, the consumed K, the exact P/V joins, the persisted `continue_applies` V outcome (never re-derived), the original uninterrupted process-local winning owner, an eligible current root/task and BOTH halves of a0/a1/a2/a3 (full closed key/type/value/cardinality checks); it inserts the active E, the final candidate/task/hook and closed `continued` result-stage audits, the N `needed`, the D `pending(G)`, changes the task to Pending/null `block_kind` with the causal owner/session preserved, and CASes J to `continued`. D is inserted when absent or CASed from its EXACT retired previous G only; a pending/admitted pointer, an existing live v1 envelope or an existing v2 envelope for the candidate is never replaced. A required audit failure rolls the whole final transaction back, retaining the previously committed consumed K/P/V/a0..a3 and the in-progress task, and a genuine finalization failure poisons only the authentic winning owner and selects C3d1 refusal-only housekeeping (with a durable failed-stage obligation and a process-local safe fallback when that diagnostic cannot be written); no remint, re-evaluation or reopen follows, and persisted UUIDs never restore liveness. Exact successful causal replay authenticates the final evidence read-only (`already_continued`) or goes to post-final housekeeping; it never allocates another E/N/D, spends E or resets N, and a later legitimate selector activation never invalidates the pinned final tuple. A SEPARATE `Database.settle_authority_policy_v2_continuation_receipt` transaction/read authenticates the complete final J/K/P/V/E/N/D and final audits/attribution before settlement: a genuine recovery requires the REAL exact root/agent/recovery_session_id/accepted_result_id/accepted_result_session_id Q and CASes `callback_accepted` -> `callback_consumed` together with the required closed completion and `authority_policy_v2_recovery_settled` audits; an already-`callback_consumed` exact Q independently authenticates the complete existing settlement/final evidence read-only and returns `already_settled_exact` (a transition-only false return is never treated as absence and missing evidence is never synthesized); explicit recovery without a matching Q returns bounded `receipt_missing`; and a genuine ordinary absence stays ordinary and requires the real ordinary completion evidence at the accepted completion-consumer seam, never a fabricated recovery-shaped completion. Identity-scoped audit rows are enumerated before closed-payload checks so a different code/discriminator cannot hide a duplicate. A failed settlement retains Pending/E/N/D/J and `callback_accepted` and permits ONLY exact settlement retry, never policy work or a mint. `runtime/orchestrator/authority_policy_store.py` exposes thin forwarders (`finalize_v2_continuation`, `settle_v2_continuation_receipt`, `get_v2_continue_envelope`, `get_v2_recovery_notification`, `get_v2_root_dispatch`) that never commit. Publication, generation admission, envelope spend, the startup/reaper/run-step automatic discovery wiring and the production authority-hook continuation remain UNIMPLEMENTED: the shipping hook still fail-closes to ESCALATE and the dual-text feature remains unaccepted. `tests/test_authority_v2_finalization_settlement.py` covers the finalize/settle success, replay, failure and negative matrices at the real Database/store seam; `tests/test_authority_v2_shipping.py` drives the same seams against the real held-launch subprocess CLI admission evidence (explicitly holding result consumption, not claiming live continuation or enqueue). C3d2 correction (same unmerged draft PR): the exact post-final causal replay AND settlement now authenticate the COMPLETE durable evidence read-only through ONE shared post-final authenticator — exact J/R/K/P/V joins, authentic immutable binding plus pinned release/activation/selector history, persisted assessment/decision identity, consumed candidate/evaluation, BOTH halves of a0..a3, and the complete final E/N/D + final audit set — while deliberately NOT requiring the pre-final live-owner token, an `in_progress` pre-final task, a fresh mechanical-eligibility re-derivation or today's selector equality; a deleted/mutated/duplicated/foreign admitted or prior-stage audit, or a changed `task_results` session/decision, returns bounded pending/refusal read-only with no Q/audit/allocation change even on reopen, and settlement additionally authenticates the actual Pending causal owner/cancellation and E/N/D stage. Settlement audit contents are bound value/type-sensitively (`true` never equals integer `1`) to the exact authenticated attempt/candidate/envelope/notification/generation/result/root/agent/session and the actual persisted completion projection, and potentially identity-related completion/settled rows are enumerated BEFORE discriminator filtering so malformed/conflicting/duplicate rows cannot disappear into an apparent absence (unrelated genuine historical sessions are never duplicates); corruption refuses read-only and a consumed Q stays consumed. Initial `callback_accepted` settlement validates the coherent pre-state — no settlement-owned completion or settled evidence for the exact result — before its atomic Q+completion+settled write, and targeted failure injection proves rollback retains the accepted Q and every prior final row for one safe exact retry. Ordinary completion evidence is now scoped to the actual causal result/session: the real `Orchestrator._log_step_result` producer adds the v2-only `_result_row_id`/`_result_session_id` attribution at the completion seam (the v1/legacy path and `AuditLogger` bytes are unchanged) so a legitimate earlier manager completion no longer blocks a valid current result and an identical old-session body can never stand in for the current event. The C3d2 evidence-classification correction (same unmerged draft PR) enumerates every potentially related completion/settled audit row from FIELD PRESENCE before any closed typed comparison — a present `_recovery_session_id`/`_result_row_id`/`_result_session_id`/`result_id`/`session_id` or settled causal identity (attempt/candidate/envelope/notification/generation/result/session) that matches the exact result/session or is malformed (`null`/bool/int/list/string) makes the row related, a non-object body is opaque and never ordinary absence, and only a row whose every present identity is well-typed and provably a DIFFERENT value is independently unrelated. A recovery-shaped completion row is one carrying the `_recovery_session_id` key OR the settlement-only `result_id`/`session_id` keys (never the ordinary `CompletionReport` producer payload), so removing the marker key cannot turn a settlement completion into ordinary absence while the legitimate ordinary producer audit and the recovery-owned evidence still coexist. Exact settlement/replay refuse read-only on a malformed/conflicting/duplicate related row with zero Q/audit/allocation change. The ordinary settlement branch no longer vetoes on the mere existence of ANY root/manager receipt: a receipt blocks ordinary completion only when a recovery/origin/accepted-result session or accepted-result identity matches the current result/session or is malformed, or its state is nonterminal/unknown; an unrelated ESTABLISHED TERMINAL receipt (`callback_consumed`/`superseded`/`expired`/`restart_settled`) with every identity well-typed and disjoint neither supplies current authority nor vetoes ordinary completion and remains byte-for-byte unchanged. Explicit recovery without an exact Q still fails `receipt_missing`/`identity_mismatch`; no Q, audit, synthetic settlement or historical row is rewritten.
- **Authenticated publication bookkeeping (THR-229 checkpoint C3d3a, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. It adds no table and changes no released column/overloaded meaning; it completes only this PR's existing unreleased N/D state and evidence columns. `runtime/models.py` adds the closed publication vocabulary — `AUTHORITY_POLICY_V2_PUBLICATION_LEASE_SECONDS` (30), `AUTHORITY_POLICY_V2_PUBLICATION_STATES` (`needed|publishing|published`), the claim/ack/failure/invalidation status and pending-reason sets, the closed result-stage events `publish_claimed`/`published`/`publish_failed`/`publish_returned`/`invalidated`, and the bounded types `AuthorityPolicyV2PublicationTarget`, `AuthorityPolicyV2PublicationClaimOutcome`, `AuthorityPolicyV2PublicationAckOutcome`, `AuthorityPolicyV2PublicationFailureOutcome` and `AuthorityPolicyV2InvalidationOutcome`. `Database.list_authority_policy_v2_publication_targets` is a READ-ONLY discovery listing of every `needed`/`publishing`/`published` N whose current D is `pending(G)`, independent of whether a recovery transition just happened (it deliberately includes live-lease and exact already-consumed-receipt rows; actual reclaim eligibility belongs at claim). `Database.claim_authority_policy_v2_notification_publication` refuses caller transaction nesting with `transaction_owned` BEFORE it would `BEGIN`/`ROLLBACK`/invalidate the owner, and otherwise runs ONE synchronized `BEGIN IMMEDIATE` that re-authenticates the COMPLETE post-final evidence (J/R/K/P/V, pinned release/activation/selector history, the real settlement evidence, BOTH halves of a0..a3 and the final E/N/D + audit set), the actual Pending/null-`block_kind`/not-cancelled causal owner/session and the pointer G; the read-only seam is the shared post-final authenticator with an `admitted`/`settled` allowance used ONLY by the acknowledgement classification, and settlement's own `needed`-only reader is untouched. It CASes `needed -> publishing` or reclaims `publishing`/`published` ONLY after the bound daemon-process identity differs from the persisted publisher boot (verified death/restart) or the 30-second server-clock lease expired — never a caller allow boolean and never elapsed time alone to declare a still-live publisher dead — then increments the bounded positive publication attempt (overflow refuses), records the bound publisher boot/lease, updates the canonical row/columns consistently and appends exactly one closed `publish_claimed` audit for the exact G/P in the SAME transaction. Failure restores the previous state/counter/lease and appends nothing; a live duplicate claim or wrong tuple is a bounded refusal that never poisons the winner. `Database.acknowledge_authority_policy_v2_notification_publication` requires the exact G/publisher boot/P and state `publishing`, authenticates the retained closed claim audit and the Pending causal owner, CASes to `published` with exactly one closed `published` audit atomically, and returns an exact read-only retry without allocating or regressing state; a stale P/boot can never acknowledge or reset a newer claim. An `admitted`/`settled` notification whose consumer outran acknowledgement is NEVER returned to `published` — only a `publish_returned` observation for the exact prior P would be eligible, and because the real generation-admission producer does not exist in this checkpoint that complete required evidence cannot be authenticated, so the path refuses fail-closed with `admission_evidence_missing` and zero mutation (final real producer race proof belongs to the admission unit). `Database.record_authority_policy_v2_notification_publication_failure` is the bounded audited queue-failure bookkeeping: it requires the exact G/P/boot and `publishing`, keeps the monotonic attempt number, clears the publisher lease so the notification stays safely reclaimable, and appends one closed `publish_failed` audit; if recording fails the prior publishing lease/state survives reclaimable, and an exact retry authenticates the retained evidence read-only. `Database.invalidate_authority_policy_v2_notification_generation` authenticates the complete post-final evidence (allowing the root pointer to have legitimately advanced to a replacement generation), atomically marks the exact old G `invalidated` and retires D ONLY while it still names that G with one closed `invalidated` audit; it never mutates a cancelled/terminal/replacement task, retires replacement generation B, resets N to `needed`, spends E or creates any escalation/notification-routing side effect, and a failed audit rolls its own invalidation changes back. None of these writers performs a queue call. `runtime/orchestrator/authority_policy_store.py` exposes thin forwarders (`list_v2_publication_targets`, `claim_v2_notification_publication`, `acknowledge_v2_notification_publication`, `record_v2_notification_publication_failure`, `invalidate_v2_notification_generation`) that never begin/commit/rollback. The prerequisite ordinary-evidence correction is included: `_v2_completion_row_is_ordinary_related` now evaluates EVERY present causal reference before declaring a completion row independently unrelated, so a well-typed-but-DIFFERENT `_result_row_id` can no longer veto a surviving exact or malformed session reference (both manager step23-probe conflicts refuse). Production automatic publication and generation admission remain the NEXT unit: the real publisher plus the non-bypassable generation-admission/fallback fences, envelope spend, the startup/reaper/run-step discovery wiring and the editable-pair editor/browser path remain UNIMPLEMENTED, the shipping authority hook still fail-closes to ESCALATE and the dual-text feature remains unaccepted; these storage methods alone confer no launch authority. `tests/test_authority_v2_publication_bookkeeping.py` drives the discovery/claim/ack/failure/invalidation seams at the real Database/store boundary, and the Cartesian ordinary-evidence relatedness matrix lives in `tests/test_authority_v2_finalization_settlement.py`. The C3d3a correction (same unmerged draft PR) adds the read-only SETTLEMENT PROOF required before every publication write — genuine ordinary completion evidence OR the exact real `callback_consumed` Q plus BOTH complete settlement audits, with the exact-recovery branch requiring the ONE potentially-related receipt to BE that exact Q (a second related/malformed/nonterminal receipt is a conflict; an unrelated established-terminal receipt stays non-blocking) and never calling a transaction-owning settlement method or transitioning Q; it authenticates the exact retained `publish_claimed` and every state-required `published`/`publish_failed` event before reclaim/initial failure/exact replay/acknowledgement (missing/mutated/duplicated/conflicting evidence refuses with no repair-by-reinsertion and a null lease alone is never proof); it enumerates and classifies potentially related publication events BEFORE any attempt/generation/P discriminator filtering (null/missing/mistyped attempt or wrong/malformed candidate/result/envelope/notification/generation reference is a related conflict, an opaque body is never absence, and only an all-present-well-typed-distinct row is prior `P` history); every retained generation's claim history must be the complete, duplicate-free closed set `{1..P}` of authentic `publish_claimed` events, so an appended row whose only defect is a well-typed impossible zero/negative/future `P` (or `P` plus a null generation) can no longer masquerade as legitimately distinct prior history while a genuine earlier attempt remains valid after a real reclaim; and invalidation requires an affirmative durable cancellation/replacement-owner/stale-generation-displacement cause read from current state, so a null/malformed identity field alone refuses and a healthy exact owner/pointer stays publishable. The publication-stage shipping proof in `tests/test_authority_v2_shipping.py` extends the fresh and full historical-migrated real owned-RuntimeDir/two-agent subprocess CLI -> HTTP -> persisted-result venues through finalized/exact-settled -> discovery/claim/acknowledgement with missing settlement and missing retained claim refusals; the methods stay dark and the provider launch is the sole external-launch double.
- **Real publication, atomic generation admission and mandatory fallback fences (THR-229 checkpoint C3d3b, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. It adds no table, changes no released column/overloaded meaning and rebinds no identity. `runtime/orchestrator/authority.py` gains the real publisher `publish_authority_policy_v2_notifications(orch, queue, *, limit=32)`: it independently discovers every publishable generation (including exact already-consumed recovery receipts), claims through the authenticated C3d3a public seam, and ONLY after a winning claim calls the real `TaskQueue.put_nowait(slug, root, metadata={"authority_v2_generation": G, "publication_attempt": P})` OUTSIDE any DB transaction, then authenticates and acknowledges the exact claim; `P` is diagnostic and only `G` is admission authority, a failed claim performs NO queue call, a queue exception uses the bounded audited failure path with the prior claim still safely reclaimable, and an acknowledgement/audit failure retains a replayable lease/state. `runtime/models.py` adds the closed generation-admission vocabulary (`AUTHORITY_POLICY_V2_GENERATION_CLAIM_*`, `AUTHORITY_POLICY_V2_ADMISSION_SETTLEMENT_*`, the `generation_claimed`/`notification_settled` result-stage events and the bounded `AuthorityPolicyV2GenerationClaimOutcome`/`AuthorityPolicyV2AdmissionSettlementOutcome`). `Database.try_claim_v2_continuation_generation` is the non-bypassable atomic fence: one synchronized `BEGIN IMMEDIATE` authenticates the complete post-final evidence, the settled final generation, the exact tagged token G, D `pending(G)`, N `publishing` OR `published` (a real consumer can outrun acknowledgement), no prior reservation and the causal Pending/null-`block_kind`/not-cancelled owner; it atomically writes N `admitted` + `next_session_id`, D `admitted(G)`, the task to `in_progress` with a normal monotonic step increment and the reserved owner/session and exactly one closed `generation_claimed` audit, rolling ALL fields back on any failure so the durable owner stays Pending and a duplicate publication may retry. The separate `Database.settle_v2_continuation_generation_admission` CASes `admitted` -> `settled` with one closed `notification_settled` audit; only the original uninterrupted winning owner may settle then launch with that exact runtime session (`run_step` passes `runtime_session_id`), a settlement failure holds dispatch and permits only exact settlement retry, and a reopened/replayed path settles exact evidence or reads it back (`already_settled_exact`) but NEVER dispatches or repeats admission — no exactly-once external-process-launch guarantee is claimed. `Database.try_claim_for_step` now atomically refuses a pending v2 root-dispatch pointer (the ordinary claim cannot win on status/`block_kind` alone), and `run_step` admits a TAGGED queue item ONLY through the generation claim; a malformed/present-null/empty/stale/mismatched token refuses with no ordinary fallback, while `run_step`/`TaskQueue`/`Dispatcher` preserve metadata unchanged and never adopt G at dequeue. The consumer-outruns-acknowledgement path now validates the COMPLETE admitted/settled stage pre-state — the bounded contiguous `{1..P}` claim/observation history, the exact G/D/next-session reservation and `generation_claimed` (the ABSENCE of `notification_settled` while `admitted`, and exactly the authentic `notification_settled` once `settled`) and the genuine ordinary-or-exact-recovery settlement proof — and classifies every possibly-related `publish_returned` observation three ways before any attempt/G/P/boot filtering: zero authentic-related observations permits exactly ONE insert after every prerequisite authenticates, one byte/type/closed-shape-correct observation is a read-only exact replay, and any malformed/duplicate/conflicting/opaque/extra-key/foreign-with-an-exact-reference observation refuses with the exact prior residue; a single authenticator's false read is never permission to append and N/D/task are never regressed. The mandatory-fence producer inventory is refreshed; the common DB-aware enqueue entry may resolve G only at ENQUEUE time, and the claim/database fence remains the non-bypassable backstop. The real publication/admission venue is proven in `tests/test_authority_v2_shipping.py` over BOTH a fresh and a full historical-migrated owned RuntimeDir/two-agent subprocess CLI -> HTTP -> persisted-result venue through the ACTUAL publisher -> real `TaskQueue` -> `Dispatcher`/`run_step` -> reserved-session `_run_agent` -> held external launch (the provider launch remains the sole external-launch double; labelled publication/admission-stage fixture proof with the earlier consumer staged, not final authority-consumer acceptance); `tests/test_authority_v2_generation_admission.py` covers the atomic claim/settlement, duplicate and two-connection one-winner contention, the generation negative matrix, cancellation/replacement owners, the ordinary `try_claim_for_step`/direct-run-step fence, audit-failure rollback with exact residue, caller-transaction nesting refusal and the acknowledgement race. The prerequisite bounded-history correction closes a real resource defect: `_v2_authenticate_publication_claim_history_uncommitted` now proves the contiguous `{1..P}` claim set by cardinality plus already-established unique in-range keys instead of `set(range(1, P+1))`, so a coherent huge `P` (e.g. 2147483646) refuses with work proportional to the retained events and the exact DB unchanged. The C3d3b correction (same unmerged draft PR) makes admission and settlement authenticate their COMPLETE row/evidence pre-state instead of one sentinel per symptom: generation admission additionally requires the complete retained publication evidence for the exact generation (the bounded contiguous `{1..P}` claim history, the bound P/publisher boot and every state-required `published`/`publish_failed`/`publish_returned` observation, each classified before any discriminator filtering) AND the provable ABSENCE of any prior related `generation_claimed`/`notification_settled` event, so a deleted, duplicated, preexisting, foreign, malformed, opaque or extra-key row refuses with the exact prior residue and is never repaired by reinsertion; admission settlement reuses that retained publication evidence plus the genuine ordinary-or-exact-recovery settlement proof at every boundary, requires coherent absence of settlement-stage evidence before the first `admitted -> settled` transition, treats an already-recorded claim/settled event inconsistent with current N/D as a conflict and replays `already_settled_exact` read-only. Targeted fault injection now covers the exact N/D/task UPDATE, `generation_claimed`/`notification_settled` audit and commit boundaries — including invalidation's LATER root-dispatch UPDATE and reclaim failures that retain the previous P/boot/lease/claim/failure history — and the fresh AND full historical-migrated shipping venues add missing-retained-claim and missing-ordinary-completion negatives that launch nothing and fall back to no ordinary path. The omitted admitted/settled acknowledgement branch correction (TASK-8555) applies that same complete-pre-state rule to the acknowledgement: a deleted ordinary completion, a preexisting `notification_settled` while `admitted`, a conflicting current-P `publish_failed`, and any conflicting/duplicate/malformed/null/wrong-type/extra-key/opaque related `publish_returned` observation refuse with the exact admitted/settled residue and never fabricate an observation; a `publish_returned` audit-insert or commit failure rolls back for one safe exact retry; and BOTH shipping venues add a barrier-established (never elapsed-sleep) admission-before-corruption acknowledgement negative proving the refusal, the unchanged admitted reservation/N/D and no continuation launch. The automatic v2 authority continuation hook stays fail-closed and envelope spend, the full consumer/reaper/startup-recovery integration and the editable-pair editor/browser path remain UNIMPLEMENTED; the dual-text feature remains unaccepted.

- **Atomic next-result spend and the result-keyed READY decision receipt (THR-229 checkpoint C3d3c1, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. It adds no table, changes no released column/overloaded meaning and rebinds no identity: it completes only this PR's unreleased additive v2 representation. `runtime/models.py` adds the closed decision-receipt vocabulary (`AUTHORITY_POLICY_V2_DECISION_STATES` `ready|claimed|applied|refused` plus the forward-only transition map), the spend outcome vocabulary (`AUTHORITY_POLICY_V2_SPEND_STATUSES` `spent|already_spent_exact|spend_pending`, its bounded pending reasons, the `spent` result-stage event) and the bounded `AuthorityPolicyV2SpendOutcome`; the unreleased `authority_policy_v2_continue_envelopes` row gains a nullable closed `decision_state` (NULL exactly while `active`, non-NULL and forward-only once `consumed`) with a partial UNIQUE index on `spending_result_id`. A consumed E carrying a non-null `spending_result_id` IS the RESULT-KEYED spending receipt for that exact reserved-session result; its `decision_state=ready` is the durable READY decision receipt the ordinary consumer will later CAS through `claimed` to `applied`/`refused`. `Database.spend_authority_policy_v2_continue_envelope` refuses caller transaction nesting with `transaction_owned` BEFORE it would `BEGIN`/`ROLLBACK`/invalidate the owner, and otherwise runs ONE synchronized `BEGIN IMMEDIATE` that re-authenticates the COMPLETE post-final causal evidence (the exact J/R/K/P/V joins, authentic immutable binding plus pinned release/activation/selector history — TODAY'S selector equality is deliberately NOT required, so a later legitimate activation never invalidates this pinned generation — the consumed candidate/evaluation, BOTH halves of a0..a3 and the complete final E/N/D + audit set), the exact tagged generation G (`G` is authority, `P` diagnostic) with D still naming it, the reserved same-root manager session, the complete retained publication evidence and the genuine ordinary-or-exact-`callback_consumed` settlement proof (including a conflicting potentially-related Q), and the immutable spending result R2: a real `task_results` row for the exact root/agent/reserved session, distinct from the causal result, whose EXACT normalized report identity (the type/field-presence-preserving projection the shipping callback admission persists) is derived from the retained row and bound into the durable `spent` receipt as `report_digest`, with its own authenticated v2 launch binding, plus the separated proof that ZERO potentially-related `spent` events exist. It then atomically CASes E `active` -> `consumed` with `spending_result_id=R2` + `decision_state='ready'`, D `admitted(G)` -> `retired(G)` and exactly one closed `spent` (`ax`) audit, retaining the task, R2, N, K/P/V, causal J and every prior audit byte-for-byte and performing NO consumer call, task-status effect, child creation or queue call. An exact same-R2 retry (including after a reopen) authenticates the consumed/retired/ready receipt read-only as `already_spent_exact` with no remint or dispatch, re-deriving R2's normalized report digest from the persisted row so ANY material report-field drift (a syntactically valid changed decision body or any summary/status/confidence/verdict/output-path/risks/wait-ID/local-CI change) refuses; a causal-R replay, a foreign/wrong-session/malformed/nonexistent R2, a missing/null/mistyped generation or session, a replaced/cancelled owner, a conflicting or malformed/opaque/duplicate/extra-key `spent` event, a spent-shaped related row whose stage discriminator is absent/null/mistyped/unknown (a non-`spent` row is skipped ONLY when it is a recognized other result stage carrying that stage's exact closed key set), a distinct later R2 receipt and a stale-A-after-replacement-B pointer all refuse with the whole transaction rolled back (E active, D admitted, R2 retained, decision unapplied) and no repair-by-reinsertion. Every distinct E UPDATE, D UPDATE, audit-INSERT and COMMIT failure is proven to roll the entire transaction back before one safe exact retry; two real connections with a deterministic `BEGIN IMMEDIATE` rendezvous prove ONE spend and ONE receipt/audit. A post-spend late acknowledgement carrying the ACTUAL retained publication attempt/boot refuses as bounded `stale_claim` and a post-spend invalidation is exactly `not_invalidatable`, in both cases with the complete durable residue preserved and no pointer/task regression. `runtime/orchestrator/authority_policy_store.py` exposes the thin `spend_v2_continue_envelope` forwarder that never begins/commits/rolls back. The writer is deliberately DARK (no automatic caller) and produces no launch authority; the ordinary decision consumer (ready -> claimed -> applied/refused), the ready->claimed CAS and decision audits, the common run_step/startup/reaper wiring and the shipping consumer path remain the NEXT serial unit, and the dual-text feature remains unaccepted. `tests/test_authority_v2_envelope_spend.py` drives the healthy/replay/refusal/boundary/contention/post-spend matrix at the real Database/store seam, and `tests/test_authority_v2_shipping.py` extends the fresh AND full historical-migrated owned-RuntimeDir/two-agent subprocess CLI -> HTTP venue through the actual reserved next invocation + real R2 callback admission to an explicit public spend invocation (stage proof with a direct storage handoff; the C3d3c2 checkpoint above adds the real common-consumer path end to end over the same venue), including a deterministic spend-audit failure that preserves the retained R2 and then succeeds on the exact retry, plus labelled fixture-corruption A/B negatives at the actual spend boundary (a null-discriminator related audit row and a changed R2 report body over the genuine shipping admission), each followed by a restored healthy exact replay.
- **Ordinary decision-dispatch claim/acknowledgement/refusal and the coupled common-consumer gate (THR-229 checkpoint C3d3c2, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. It adds no table, changes no released column/overloaded meaning and rebinds no identity; it completes only this PR's unreleased additive v2 representation. `runtime/models.py` adds the closed decision-dispatch vocabulary (`AUTHORITY_POLICY_V2_DECISION_CLAIM_STATUSES`/`_PENDING_REASONS`, `AUTHORITY_POLICY_V2_DECISION_ACK_STATUSES`/`_PENDING_REASONS`, `AUTHORITY_POLICY_V2_DECISION_REFUSAL_STATUSES`/`_PENDING_REASONS`, the closed result-stage events `decision_claimed`/`decision_applied`/`decision_dispatch_interrupted`, and the bounded `AuthorityPolicyV2DecisionClaimOutcome`/`AuthorityPolicyV2DecisionAckOutcome`/`AuthorityPolicyV2DecisionRefusalOutcome`). The consumed continuation envelope (E) carrying a non-null `spending_result_id` IS the result-keyed decision receipt (R2); its closed, forward-only `decision_state` (`ready|claimed|applied|refused`) is the durable single-use token. `Database.claim_authority_policy_v2_decision_dispatch` refuses caller transaction nesting with `transaction_owned` BEFORE it would `BEGIN`/`ROLLBACK`, and otherwise runs ONE synchronized `BEGIN IMMEDIATE` that authenticates the COMPLETE spent result-keyed receipt (the exact post-final J/K/P/V/E/N/D + final audit set, the retired exact generation, the settled notification, the complete retained publication evidence and genuine ordinary-or-exact-`callback_consumed` settlement proof, both `generation_claimed`/`notification_settled` admission events, R2's own authenticated launch binding, the exact `spent` audit whose closed `report_digest` still re-derives from the persisted R2 row, and the still-current nonterminal reserved invocation — `in_progress`/null `block_kind`/not cancelled/assigned owner/`current_session_id == next_session_id`), proves the ABSENCE of any potentially-related decision event, then CASes `decision_state ready -> claimed` with exactly one closed `decision_claimed` audit. ONLY that winning `claimed` return authorizes exactly ONE ordinary consumer entry; a duplicate/restarted `claimed`, an `applied`/`refused` receipt, a conflicting/opaque/malformed/missing identity or a replaced/cancelled owner returns bounded `decision_pending` and never authorizes the consumer, while a failed claim/audit rolls back to `ready` and permits an exact claim retry without spending or evaluating again. `Database.acknowledge_authority_policy_v2_decision_dispatch` re-authenticates the complete spent receipt plus the single closed historical `decision_claimed` event and CASes `claimed -> applied` with exactly one closed `decision_applied` audit; it deliberately does NOT require, restore or regress the old task/owner/blocked projection (the ordinary consumer's independently committed task/child/effect rows and any generation B are preserved byte-for-byte), an exact applied replay is read-only `already_applied_exact` and NEVER a second consumer call, and a failure leaves the receipt `claimed`. `Database.refuse_authority_policy_v2_decision_dispatch` is the audited `decision_dispatch_interrupted` interruption refusal for an exception/restart after a committed claim (before the effect, after an independently committed task/child effect, or after a failed acknowledgement): it preserves the spent E, the causal J/K/P/V evidence and every already-committed effect, escalates ONLY the still-current nonterminal reserved invocation with the closed diagnostic (a cancelled/terminal/replaced task and any generation B are preserved exactly, with no task mutation), retires/settles only the exact owned obligation and never mutates a replacement pointer, CASes `claimed -> refused` with exactly one closed `decision_dispatch_interrupted` audit, and an exact refused replay is read-only; a failed refusal audit/commit rolls back only this transaction, retaining the discoverable `claimed` state and a bounded truthful pending outcome. `Database.get_authority_policy_v2_decision_receipt_for_result` is the read-only receipt lookup (`None` only when no consumed envelope carries that exact spending result; a corrupt canonical body returns `{"corrupt": true}`) and `Database.authority_policy_v2_decision_result_report_binds` normalizes BOTH sides through the established shipping representation (`completion_report_from_result_row`: the persisted `confidence_score` column is the model `confidence` field and the persisted raw `decision_json` is parsed into `NextStep` with the reserved carrier stripped), so the complete materially-consumed decision (not merely its action — `done.summary`, `delegate.agent`/`prompt`/`then`/`children`/`revisit_of_task_id`/`attachments`, etc.) plus every summary/status/confidence/verdict/output-path/risks/wait-ID/local-CI field must match the retained R2 row, while the persisted full material digest stays separately bound into `spent`. The COMMON consumer is now guarded: `runtime/orchestrator/run_step.py` separates `_consume_completion_report` (the single guarded entry used by ordinary completion, accepted recovery, the startup sweep and the zombie reaper) from `_consume_completion_report_body` (the unchanged EXISTING normal decision body). Before ANY normal task mutation/orchestration audit/decision/delegate/enqueue effect the guard classifies the completion against the REAL persisted v2 lineage through `Database.authority_policy_v2_completion_dispatch_context` (never reader absence or a mock's `None`): `no_v2` only when the root has no v2 lineage at all (a pre-final, attempt/candidate-only lineage is unchanged ordinary); `causal` (the causal result of a finalized generation) is continuation bookkeeping only and never spends, remints or re-enters the normal effect; the exact active reserved next result R2 is atomically SPENT through the EXISTING writer and then claimed exactly once; an exact `ready` receipt first requires the supplied report to bind to the retained R2 body before claiming; a restarted `claimed` receipt performs audited interruption refusal; `applied`/`refused` are read-only; a fully terminal generation classifies a completion as `later` (ordinary-capable) ONLY when it is a GENUINE later result — the persisted result's agent/session are the root task's current durable owner (`assigned_agent`) and session (`current_session_id`), both non-empty, and the row is genuinely later than every retained terminal v2 evidence row — and the common gate then additionally requires the supplied report to match that exact result's persisted material identity before any ordinary effect; a bare/empty/wrong-agent session, a wrong owner, a merely larger/latest row id, terminal flags or an absent exact receipt are never sufficient and stay a fail-closed `foreign` skip, while every retained consumed envelope's exact terminal decision evidence (the retained `decision_claimed` plus `decision_applied`/`decision_dispatch_interrupted` events and the re-derived `spent` receipt) must still authenticate so an older live/corrupt generation behind a terminal pointer keeps the lineage live; an identity with no real persisted result row for a root that HAS v2 history (including `None`/bool/string/unknown integer) is a fail-closed `foreign` skip, never the ordinary/v1 absence path; and every other unmatched/malformed/foreign identity is a fail-closed skip, never ordinary permission, and a classification read failure is fail-closed. The winning caller retains the exact CAUSAL receipt identity from `AuthorityPolicyV2CompletionDispatchContext` and acknowledges/refuses with THAT identity (never the reserved spending result id and never post-effect task/owner/pointer state); a bounded pending writer outcome leaves the receipt recoverable and never permits a second consumer. Every first-write/replay authenticates the COMPLETE closed decision-event set, so a mutated preceding `decision_claimed` (e.g. a zeroed `report_digest`) refuses exactly like a mutated final event, and a historical acknowledgement/refusal still settles generation A after the consumer legitimately advanced the root pointer to generation B (A's retirement is proven by the exact `spent` audit, not by a still-current D). An exception raised by the normal body after the claim triggers the refusal bookkeeping and is re-raised; an acknowledgement/audit failure leaves the receipt `claimed` and never re-runs the consumer. The accepted-recovery special branches route any spent next-result receipt through this same guarded entry so they cannot bypass it. The automatic v2 authority continuation hook stays fail-closed/DARK: the broader startup/reaper/queue producer discovery and the editable-pair editor/browser path remain UNIMPLEMENTED and the dual-text feature remains unaccepted. `runtime/orchestrator/authority_policy_store.py` exposes thin `claim_v2_decision_dispatch`/`acknowledge_v2_decision_dispatch`/`refuse_v2_decision_dispatch`/`get_v2_decision_receipt_for_result`/`v2_decision_result_report_binds` forwarders that never begin/commit/roll back and perform no consumer/queue/external-process call. `tests/test_authority_v2_decision_dispatch.py` drives the healthy claim/ack/replay, interruption refusal, cancellation preservation, caller-nesting, boundary-injection, two-connection one-winner and corrupt/malformed-prior-event matrix plus the common-consumer gate classification at the real Database/store seam.
- **Common DB-aware task enqueue boundary and direct-producer convergence (THR-229 checkpoint C3d4a, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. It adds no table, changes no released column/overloaded meaning and rebinds no identity. `Database.classify_authority_policy_v2_root_dispatch_for_enqueue` is a narrow synchronized read that distinguishes a provably ABSENT root-dispatch pointer (unchanged ordinary enqueue) from a live `pending(G)` generation (route through the authenticated publisher), an `admitted` generation (never relaunched by an ordinary enqueue), a `retired` pointer (legitimate later work stays ordinary) and every malformed/present-corrupt or unreadable state (fail closed) — the existing `get_authority_policy_v2_root_dispatch` collapses absent and malformed into one `None`, which is never ordinary enqueue permission. `runtime/orchestrator/authority.py` adds `enqueue_task_generation_aware(orch, queue, slug, task_id, *, metadata=None, ordinary_enqueue=None)`, the common entry: it resolves the TARGET root's OWN durable generation at production time and either ordinary-enqueues (absent/retired, preserving trigger metadata such as `job_terminal`/`triggering_job_id`), publishes through the existing `publish_authority_policy_v2_notifications(..., root_task_id=...)` seam (real claim -> raw `TaskQueue.put_nowait` outside any transaction -> exact ack), or refuses with ZERO queue calls (admitted/malformed/unreadable, a pending root whose claim did not win, or a pending root with no publishable notification). Request metadata can never manufacture/replace G and a parent's G is never copied onto a child/successor; a publication may repeat but generation admission may not. Only a genuine durable `Database` drives the classification (a mock/duck-typed orchestrator is not permission to consult or bypass durable state and keeps the unchanged ordinary path), and each converged producer preserves its EXACT original ordinary call shape via the optional `ordinary_enqueue` callable (the direct producers' `put_nowait(slug, task_id)`, the blocked-job resume's `enqueue(..., metadata=...)`, and the runner/startup `enqueue`) so queue/drain/dispatcher forwarding and `task_done` accounting are unchanged. `runtime.daemon.runner.enqueue_task` IS that boundary, and the direct producers converge through it: the `run_step` successor/self-correction/delegate/feedback/chain/parent-wake/job-unblock/fanout sites, the `authority` v1 ordinary continuation producer, the `tasks` route successor/continue sites and the `__main__` startup sweep ordinary/parked enqueues. The raw `TaskQueue.put_nowait` remains the transport (including the authenticated publisher's own call and the separate dream/schedule/wake queues, which are different domains), and the run-step/DB claim remains the non-bypassable backstop. Accepted R4 also requires the DEQUEUE side to request independent discovery/publication: when `run_step`'s UNTAGGED ordinary `try_claim_for_step` refuses because the target root has a live `pending(G)` pointer, `run_step_impl` calls the EXISTING `publish_authority_policy_v2_notifications(orch, queue, root_task_id=...)` publisher and returns WITHOUT admitting or launching anything -- never adopting G, never rewriting stale/untagged metadata and never falling back to an ordinary launch. Only that specific pending classification requests publication (a genuine ordinary CAS loser and every absent/admitted/retired/malformed/unreadable state request NOTHING), and only a genuine durable `Database` drives it. `tests/test_task_enqueue_boundary.py` covers the classification and every boundary outcome against the real Database/publisher seams and adds Part C interleavings over a SECOND genuine `Database` connection on the same file: cancellation and authenticated replacement-generation-B pointer advance between producer classification and the publication claim both refuse with ZERO queue calls, plus two loaded orgs with the SAME textual task id resolved through the real runner entry/queues into their OWN Database/generation/queue; `tests/test_authority_v2_generation_admission.py` adds the real dequeue seam (a raw untagged pending item requests publication while a separately consumed fresh TAGGED item admits and launches exactly once, and a delayed tagged generation A consumed after replacement B is pending refuses at the tagged admission fence). `tests/test_authority_v2_shipping.py` adds the delegate+failed-acknowledgement branch over both fresh and full historical-migrated venues with actual enqueue/body-entry counters that must not increase across reopen/refusal/replay, and the REAL post-terminal LATER lifecycle over both venues continuing past the child/root-relaunch precondition into the actual relaunched ROOT: real manager R3 subprocess CLI -> HTTP -> persisted root result -> the REAL common consumer's terminal-v2 `later` classification (asserted from the real classifier BEFORE any effect) -> the existing normal body (`done`) exactly once, with no manual owner/session/status patch, unchanged generation-A evidence and no extra enqueue; the changed-supplied-report report-binding refusal is asserted at the real gate (zero normal-body/task/child/queue effect), distinct from the route's own duplicate suppression, with actual-ROOT unbound/wrong-agent/wrong-session/stale negatives. Checkpoint C3d4b (same unmerged draft PR) wires the accepted POST-FINAL receipt settlement and pending-generation publication into the ACTUAL recovery/startup seams: `runtime/orchestrator/authority.py` gains `reconcile_authority_policy_v2_post_final`, which derives the immutable finalized envelope (E) and — through the new read-only `Database.get_authority_policy_v2_settlement_receipt_identity` — the exact real recovery receipt Q, then settles through the EXISTING public `settle_authority_policy_v2_continuation_receipt` writer (genuine recovery for an exact Q, read-only authentication for an already-`callback_consumed` Q, and the ordinary completion-evidence branch when no Q exists) and independently discovers/publishes every `needed`/`publishing`/`published` `pending(G)` notification through the EXISTING authenticated publisher; it evaluates, remints, spends and launches nothing and never runs the ordinary decision body, and a missing/conflicting settlement proof refuses with the prior residue and zero queue calls. `_consume_accepted_completion_recovery` routes a `causal` classification to that helper instead of the common consumer's skip, `runtime/daemon/__main__._build_state` binds the REAL owning-process identity (`OrgState.authority_v2_origin_boot_id` via `OrgState.bind_authority_v2_owner`) and the existing server-owned `_strict_permission_surface_digest` reader before recovery, and a startup-wide publication pass with `limit=None` covers every eligible root (no first-32 starvation); a same-boot unexpired lease is never stolen and an already admitted generation is never republished (the tagged dequeue claim stays the only admission). The pre-final automatic authority hook, refusal/reaper orchestration and the editable-pair editor/browser path remain the NEXT units; the hook stays fail-closed/DARK and the dual-text feature remains unaccepted. `tests/test_authority_v2_post_final_reconciliation.py` drives the real `_consume_accepted_completion_recovery`/`_sweep_on_startup` callers across accepted/consumed/ordinary settlement, missing/conflicting-proof refusal, lost-queue/new-boot republication, same-boot live-lease and a real queue-failure retry; `tests/test_task_enqueue_boundary.py` finishes the two-org proof by draining both same-id orgs through the real `Dispatcher.run_step`; `tests/test_authority_v2_shipping.py` adds the same accepted-recovery seam over both fresh and full historical-migrated owned RuntimeDir venues plus a corrupt-settlement refusal. C3d4b correction (same unmerged draft PR): the post-final outcome is authenticated, never inferred from an empty discovery. An already-`callback_consumed` receipt whose notification/pointer/claim evidence was deleted, hidden or made malformed is no longer reconciled by the absence of a refusal: reconciliation reconciles ONLY on a real writer outcome (`settled`/`already_settled_exact` from the EXISTING settlement writer) or an actual authenticated publication of the EXACT generation, so a missing N, a missing D, a hidden malformed target and a discovered-but-refused claim all return `settlement_refused` with zero queue calls and byte-unchanged durable residue. An authentic generation already ADMITTED by a real consumer (N `admitted`, D `admitted(G)`) is completed through the EXISTING `Database.settle_v2_continuation_generation_admission` writer using the immutable G and reserved session derived from durable rows, so a reopened owner finishes the exact R4 step6 bookkeeping (`notification_settled`) without ever republishing/reclaiming the admitted generation, performing a second admission, regressing task status/session/step or gaining launch authority; a corrupt admitted/settled generation refuses unchanged. `tests/test_authority_v2_post_final_reconciliation.py` adds the missing-notification, missing-pointer, admitted-unsettled and deleted-claim-audit matrix plus the same admitted/corrupt cases at the actual `_consume_accepted_completion_recovery` caller. C3d4b correction B (same unmerged draft PR): `publish_authority_policy_v2_notifications` is now per-target resilient — a raised claim, queue or acknowledgement failure is converted into a bounded receipt (`publication_claim_failed`/`publish_failed`/`publication_acknowledgement_failed`) and the pass continues, so one bad target can never starve the unrelated eligible roots discovered later (previously a single raised exception aborted the whole startup pass). A failed claim still makes ZERO queue calls, a queue failure keeps the bounded audited failure record and the prior committed settlement, an acknowledgement failure leaves the raw tagged put done with a reclaimable lease and the generation fence admitting exactly once, and a bounded refusal still cannot become a false `reconciled` result or ordinary permission. The same test file adds the real-coupling fault matrix (receipt settlement, claim, raw put, acknowledgement, generation-admission settlement), cancellation/owner-loss/malformed-identity refusals, a genuinely produced generation B progressing with old A byte-unchanged, a reopened DISTINCT `Database` + real `OrgState` (server-owned permission-digest binding) over the same persisted file rediscovering a lost queue, and 34 genuinely finalized roots proving startup publication beyond the first 32 with an early live lease refused. C3d4b receipt-discovery correction and caller/reopen completion (same unmerged draft PR): `Database.get_authority_policy_v2_settlement_receipt_identity` is converged on the caller's exact immutable E/R/session identity and the SAME potentially-related classification the ordinary settlement branch uses (`_v2_receipt_blocks_ordinary`), so an unrelated ESTABLISHED TERMINAL historical receipt no longer blocks a healthy current `callback_accepted`/`callback_consumed` Q or genuine ordinary evidence (previously any `len(receipts) > 1` was a blanket conflict), while a related partial/malformed/nonterminal/conflicting Q still refuses read-only with zero queue calls; the reader stays read-only, the public settlement writer still independently re-reads and authenticates, and no new evaluator/authenticator/table/column/dependency is added. `tests/test_authority_v2_post_final_reconciliation.py` proves synthetic and genuinely public-produced historical history (`_drive_realistic_terminal_history`), the ordinary-completion-plus-history and current-Q-beside-ordinary controls, the related/malformed/nonterminal conflict refusals, the receipt/claim/raw-put/acknowledgement/generation-admission-settlement fault matrix at the ACTUAL `_consume_accepted_completion_recovery`/`_publish_v2_generations_on_startup`/`_sweep_on_startup` callers (counting raw queue attempts separately from accepted enqueues and covering the raw-put-plus-bookkeeping-raise branch), the reopened admitted-but-unsettled/settled/corrupt `Database`+real `OrgState` proofs over the same persisted file, old-A late bookkeeping replay while authentic public-produced B is current, and the >32-root real-`OrgState` production startup pass with a real ordinary-root control. Genuine recovered subprocess CLI Part C and multi-org `DaemonState` Part D remain the following serial work; the feature is not complete and the dual-text feature remains unaccepted.
- **Automatic pre-final v2 authority hook and common-consumer handoff (THR-229 checkpoint C3d5a, unmerged draft PR878).** This is staged, not shipped: it lands on the same open draft feature PR and is not part of the running daemon until a later guarded merge. It adds no table, changes no released column/overloaded meaning and rebinds no identity. `runtime/orchestrator/authority.py::run_authority_hook` now selects the policy FAMILY from the AUTHENTICATED immutable result/session launch binding (`load_session_policy_binding`) BEFORE any legacy `load_session_policy_snapshot` handling: a `v2` binding runs the accepted claim+pin -> claim audit -> evaluation -> evaluation audit -> candidate consume -> consumed audit -> atomic final continuation sequence under the original uninterrupted live owner and then the EXISTING exact post-final reconciliation (`reconcile_authority_policy_v2_post_final`) for settlement/publication; a `legacy_static`/`db_release`/absent binding keeps the unchanged v1 path, and any mixed/unknown/malformed family evidence fails closed to ordinary `escalate` (recorded capture failure) and never enters the v1 candidate machinery. The persisted normalized `manager_self_evaluation` admitted from the real `report-completion --from-file` CLI -> HTTP -> durable result is authoritative: a caller-supplied assessment object can neither manufacture nor replace it, and the accepted pure dual assessment is derived exactly once (escalate `applies` wins; continue only when escalate is clearly `does_not_apply` AND continue `applies`; neither/uncertain/malformed defaults to durable refusal). V2 introduces no second model invocation, clause/ID/canonical-reason/keyword unlock, live-selector substitution, or adverse-review/partial-work/raw-DDL veto — those observations stay diagnostic and the retained identity/owner/session/result/boot, cancellation/replacement, active-work, budget, protected-drift, atomicity, replay and audit fences are preserved. The hook returns a new closed vocabulary (`v2_continued` / `v2_refused` / `v2_pending`) and the directly coupled common consumer `run_step.py::_consume_completion_report_body` returns without ordinary escalation, audit/notification or ordinary enqueue fallback on every v2 outcome; a committed final continuation survives pending settlement/publication, an unfinalized pre-final failure requests the EXISTING durable refusal housekeeping from the greatest committed state (a failed refusal stays a bounded discoverable pending obligation with prior state intact), live duplicates never poison the winner, old-boot/interrupted attempts are refusal-only, and an already-final replay uses exact post-final reconciliation with no remint. `tests/test_authority_v2_hook.py` drives the real `Database`/`AuthorityPolicyStore` caller matrix (family dispatch, truth table and malformed/missing uncertainty, caller-object non-replacement, arbitrary noncanonical reason, adverse/partial-work diagnostics, every newly wired stage/audit failure with exact preceding residue, refusal-commit failure then refusal-only reopen, mixed/unknown binding, cancellation between stages, pinned original binding versus a later activation, live duplicate/post-final replay), and `tests/test_authority_v2_shipping.py` proves the real owned-RuntimeDir/two-agent subprocess CLI -> HTTP -> persisted-result -> automatic hook -> stages -> finalization -> settlement -> publisher -> real `TaskQueue`/`Dispatcher`/`run_step` -> exactly one reserved next launch -> real next-result CLI callback -> existing spend/claim/normal done/applied receipt over BOTH a fresh and a full historical-migrated venue (provider launch remains the sole external double). The startup/reaper discovery-and-CAS unit is described immediately below; the editable-pair editor/browser path, main convergence and the final independent review/QA/CI gates remain UNIMPLEMENTED. The feature is not complete and the dual-text feature remains unaccepted.

- **Startup pre-final refusal discovery and exact zombie-reaper CAS (THR-229 checkpoint after C3d5a, unmerged draft PR878).** This staged unit wires only accepted recovery ownership and reaper effects. Before startup can resume, return from, or enqueue any task branch, `_sweep_on_startup` enumerates authenticated unfinalized v2 attempts, re-reads each exact housekeeping target and requests the existing durable refusal writer using the closed greatest-stage failure code. Old-boot and durably failed pre-final attempts are refusal-only; a same-current-boot live owner is fenced but never stolen; malformed discovery fails the startup task branches closed; failed refusal leaves its exact residue discoverable for refusal-only retry. Final `continued` attempts are excluded and remain owned by the existing settlement/publication reconciliation. No startup path re-evaluates policy, remints a candidate/envelope, falls back to v1/ordinary enqueue, or creates a successor. The result-present zombie path still calls the real common completion consumer first, then `Database.consume_v2_fingerprint_and_clear_zombie` uses one `BEGIN IMMEDIATE` exact-owner/result/session/marker/receipt CAS to clear only after the accepted Pending or terminal refusal effect committed; replay and every race preserve the result and diagnostic history and never run the consumer twice. The result-absent TTL path uses `Database.cancel_zombie_without_fingerprint`, whose single transaction proves exact current owner/session/marker/status/block/cancellation state and the continued absence of an exact result before atomically cancelling and auditing; a parent is woken only after that transaction commits. Both paths derive v2 from the immutable session-policy binding, preserve the unchanged v1/no-policy behavior, and fail closed on mixed or malformed evidence. `tests/test_authority_v2_startup_reaper.py` exercises representative pre-final stages, live-owner and refusal failures, the real persisted-result/common-consumer path, replay and result/marker/session/owner/cancellation races, audit/commit rollback and reopen, exact TTL cancellation, result appearance between selection and CAS, and parent wake-once semantics. The editable-pair editor/browser path, complete parity sweep, current-main convergence and final review/QA/CI remain outstanding; the feature is not complete and the dual-text feature remains unaccepted.

- **Authority-policy v2 historical-schema and recovery idempotency correction (THR-229 / issue #918).** The schema-integrity seam accepts `agent_enrollments` only when its complete table SQL, ordered `table_xinfo`, foreign keys and sole primary-key autoindex metadata exactly match one of the historical seven-, eight- or nine-column constructor/migration layouts shipped by `fa3efce7`, `31eef07a` and `e2ed0010`; it remains constraint-sensitive and every mutation, explicit extra index or unrelated object is refused. A failed pre-final stage maps its precise refusal into the closed terminal-housekeeping vocabulary before its live owner is forgotten. Terminal unrelated recovery receipts remain history, while an unrelated live receipt still fences refusal. The common consumer treats the persisted `(task_id, orchestration_step_count)` audit as its idempotency key for every ordinary, accepted-recovery, startup and zombie entry; the zombie caller also explicitly marks recovery re-entry. A committed `v2_refused` transition runs the ordinary notification/thread surfacing tail without a second escalation write. Startup uses that same post-commit tail only when refusal housekeeping returns the newly committed `refused` outcome; the `already_refused` replay is read-only and emits no second notification, thread follow-up or audit. A `v2_pending` outcome falls through the ordinary escalation CAS so the root cannot remain `in_progress` forever. Re-entry after either terminal path cannot append another step/escalation audit, notify, thread follow-up, child, candidate, evaluation or enqueue. This correction performs no DDL, migration or historical-row rewrite.

- **Versioned dual-text policy launch binding (THR-229 checkpoint C1, unmerged draft PR878).** This is staged, not shipped: it lands on an open draft feature PR and is not part of the running daemon until a later guarded merge. Eligible manager launch resolution is now selector-family-aware: it resolves the single authenticated team selector (initializing it via the transaction-owning backstop when startup did not), returns the documented static/no-active behavior for an authenticated `empty` selector, resolves a selected `legacy_v1` to exactly that selector's legacy release/activation, and resolves a selected `v2` to its immutable release/activation pair; missing/corrupt/unsupported/mixed selector state refuses rather than falling back to the newest legacy activation. A v2 launch writes one immutable `authority_policy_v2_session_bindings` row (root/team/manager/session, selector id+epoch, release id/version/digest, activation, contract, resolved provider/executor/model); a selected-v1 launch writes the unchanged legacy `authority_policy_session_binding` audit plus the additive `authority_policy_selector_session_binding` supplement in ONE transaction. Exact repeats are idempotent; changed tuples and cross-family collisions refuse. Binding reads authenticate a pin through its own bounded predecessor chain, so a later legitimate activation cannot replace or invalidate an older binding. The v2 prompt renders BOTH immutable texts under distinct `What to escalate` / `What not to escalate` labels with escalation-wins/fail-closed precedence and the exact v2 contract identity, with no clause catalogue/id, canonical continuation phrase, or keyword unlock. Legacy v1 rendering bytes/digests are unchanged. Staged debt still owed (NOT implemented here): candidate/evaluation/continuation consumers, the corrected adverse/partial/raw-DDL diagnostics and recovery/generation admission fences, and the complete editable-pair editor/browser/activation/readback path. C2 versioned strict callback/result admission and result-keyed attempts have since landed on this same branch (see the C2 checkpoint paragraph above), together with the R3 real owned-runtime subprocess shipping fixture. Until the remaining consumers land and merge, the authority hook still fail-closes an unsupported v2 consumer outcome (ESCALATE, no continuation) and the dual-text feature remains unaccepted.

- **Atomic pending parent-wake admission.** Receipt-owned cleanup tails and startup recovery can produce the same bounded parent wake concurrently. Every shipping parent-wake producer and the startup delegated/blocked-job dedupe paths use `TaskQueue.enqueue_if_absent`, which claims an ephemeral per-task reservation under the queue lock, runs generation-aware/DB-aware publication with that lock released, then clears the reservation on success, refusal, or failure. A concurrent bounded producer loses to either the reservation or an actual pending item. Dequeue atomically ends pending suppression so a later legitimate transition can wake the task again; unrelated FIFO ordering and ordinary non-coalescing enqueue are unchanged. Tests join every cleanup worker they create before another sweep, queue assertions, or shared `jobs_runner` restoration because durable settlement is not cleanup completion.
- **Graceful daemon shutdown preserves blocked-on-job recovery.** Lifespan teardown permanently stops `TaskQueue` consumers and producer admission before signalling in-flight jobs. Every job actually signalled by `terminate_all_inflight` persists `failed` with `reason=daemon_shutdown` and its real exit code; a process that exits naturally before any signal keeps its natural result, and an existing `task_ended` cause wins. The stopped queue refuses `enqueue`, `put_nowait`, and `enqueue_if_absent` (`False`), so a terminal-job hook cannot claim the waiter during teardown. The task stays `in_progress(blocked_on_job)` with its session unchanged and is resumed once on startup through Branch 3 / the lifespan scan plus pending-item dedupe. Resume headers and non-JSON `happyranch jobs show` expose the stored reason and exit code; the existing `task_resumed_from_jobs.job_outcomes` status values remain unchanged.
- **Manager authority self-evaluation (THR-181 S6a).** An eligible manager session with an active DB policy must include exactly one strict closed `manager_self_evaluation` beside its completion decision. The authenticated completion/session binding pins root, manager session, immutable release/activation, contract, and the effective manager provider/executor/model; malformed, missing, mismatched, stale, replayed, ambiguous, or low-confidence evidence fails closed with a digest-only durable diagnostic. Production does not launch a second evaluator subprocess. The manager result is advisory semantic evidence only: daemon eligibility, closed-reason and protected/mechanical fences, fresh cancellation/budget checks, single-use envelope, and all candidate/evaluation/CAS/restart rules remain authoritative. Worker and legacy/no-active-policy completions omit the field and keep their prior wire behavior.

- **Generic remote jobs S1 contracts + dark S2 persistence.** `runtime/remote_jobs/` contains
  strict immutable v1 models, canonical UTF-8 JSON/SHA-256 helpers, whole-job
  bundle validation, the corrected four-part pre-run reuse identity, and the
  closed stable-reason precedence table. It has no shipping caller yet. Runner
  S2 adds only the exact additive, staged, fail-closed SQLite schema in
  `runtime/infrastructure/remote_job_schema.py`: the six runner/workspace/
  attempt/receipt/observation/frame tables (no key-history table), exact
  indexes and composite foreign keys, and five nullable `jobs` linkage
  columns. Existing rows are not backfilled or reinterpreted. Runner
  authentication/enrollment, network transport, observation
  execution, subprocess engines/finalization, coordination/resume, CLI/web,
  deployment, and activation remain unimplemented later slices. Existing local
  jobs and `JobStatus` semantics are unchanged. V0 omits coarse manifests and
  derives the normalized observation-policy digest; its typed parser requires
  admission context for every phase/terminal frame, binds envelope and exact
  bundle-derived phase identities/fences, rejects inconsistent supplied phase
  context, and validates exact-cardinality terminal links against supplied
  canonical `PhaseFinished` receipts before deriving precedence. Public
  context-dependent models fail closed when their admission, phase-spec,
  observation-policy, or canonical-receipt validation context is absent; direct
  construction is not a second lenient validation authority. See
  `docs/agent-guides/agent-executors-and-permissions.md`.
- **Web Contract** — see the dedicated section below. Org-portability Slice A (THR-187) adds CLI-only `happyranch orgs portability-preflight <slug>` (read-only exhaustive root classification + quiescence) and founder-only `happyranch orgs reconcile-portability <slug> --from-file <abs.json>` (zombie reconciliation). No archive/export/import yet. Preflight refuses when **any** schedule is armed or firing and reports only existing controls as remedies (`happyranch todos pause|cancel` for armed; wait-for-terminal for firing; `happyranch cancel`/`jobs stop` for live work; `reconcile-portability` for a confirmed zombie). See `docs/agent-guides/features-and-invariants.md` (Org Portability) and `docs/agent-guides/orchestrator-contracts.md`.
- **GH-688 Phase 1 (thread reply-wake coalescing) is NOT deployed by merge.** Slices A/B (durable `thread_reply_delivery_state` + route/runner/API wiring) and C (web `reply_delivery` pair projection + audit lifecycle + this doc set) are merged code; the runtime only picks them up after a **daemon restart** (the deployment event). The release record/checklist lives at `docs/operations/gh-688-phase1-release-checklist.md` — it defines the deployment epoch, the two-org supersession-rate release gate (measured from the epoch, not merge time), and the post-deploy owner. Do not claim production behavior that only exists in the running daemon.
- **Thread rename/pin (THR-209 Phase 1).** Founder-only rename (`POST /threads/{id}/rename`) and pin/unpin (`POST /threads/{id}/pin`, durable `threads.pinned_at`) are presentation/organization controls. They must NEVER create a thread message/notification, touch participants/unread, or change activity timestamps; identity/URL/routing/lifecycle are immutable. Pins rank above unpinned (Pinned section, immutable numeric thread ID desc — THR-10 above THR-2, never activity/lexicographic) in open-thread views only; archived/closed views have zero pin presentation (one ordinary list, no split, no pin rank; durable pin retained for reopen/restore), and the status-less query + web "all" bucket are ordinary views too (no pin rank, no section); ordinary unpinned order stays unchanged. Audit via `thread_renamed`/`thread_pinned`/`thread_unpinned` rows (existing THR-* scope convention). See `docs/agent-guides/features-and-invariants.md` (Threads) and `docs/superpowers/specs/2026-08-25-thread-rename-and-pinning-design.md`.
- **Thread list paging (THR-289).** The same GET opts into live keyset pages with `page_size=1..100` (Web uses 50) and an optional scope-bound cursor. Each page returns org-wide Open/Archived/All/dream-origin totals, a sample time and lookahead continuation from one short owned read snapshot. Omitted pagination retains legacy `{threads}` behavior and limits. New pages use deterministic stored timestamp/BINARY-ID ties; All is globally ordered by start time and Open alone ranks pins. Browser refresh stages a replacement prefix to prior depth, commits once, rejects obsolete generations and retains usable rows/counts with updating/stale labels. Literal client search exhausts remaining pages; page failures require explicit Retry. Shared list ownership includes both real and prototype providers, and palette reads committed cache only. See the current THR-289 annotation in the Threads and Web specs.
- **Thread routing is UNCONDITIONAL (founder ruling TASK-6027).** Mention routing and the strict mention-led exchange are default and permanent — there is NO switch, route, CLI verb, UI control, or config key for either (all three were removed: the shipped per-thread `mention_routing_enabled`, the proposed per-thread `reply_exchange_enabled`, and the proposed org key `org_settings.threads.reply_exchange_enabled`). Mention routing (THR-198 Phase 2, merged Slice B): the `thread_messages.mentions_json TEXT` signal is derived server-side from `body_markdown` at write time (never client-declared) and the pure resolver `runtime/infrastructure/thread_mentions.py` routes every conversational REPLY wake at write time: one/more valid current-participant mentions → exactly that stable deduplicated set (speaker excluded); zero valid mentions (none, invalid/nonparticipant-only, self-only) → full broadcast minus speaker. The shipped `threads.mention_routing_enabled INTEGER NOT NULL DEFAULT 1` column is now an INERT legacy compatibility field: it must NEVER be read to alter behavior, written through a product surface, or exposed on the wire (persisted true/false values cannot disable routing after upgrade). Routing is write-time frozen (roster changes do not retroactively re-route minted wakes); TASK_FOLLOWUP/BOOTSTRAP are isolated and never mention-routed; GH-688 per-pair coalescing/claim/settlement/follow-on/rollback semantics are preserved. **Strict mention-led exchange (TASK-5966, founder-ratified THR-198 seq 194/195/196; design `output/TASK-5954/strict-hold-exchange-design.md`; `EXCHANGE_GRACE = 5 min`, absolute fail-open `4 h`):** additive exchange-epoch tables `thread_reply_exchange` (open/released/suppressed; partial-unique at-most-one-open-per-thread index; close_reason ∈ quiescence/max_priority_wait/founder_aborted/thread_archived/participant_removed/corrupt) + `thread_exchange_deferrals` (frozen D(E)) open on a founder-authored mention with P≠∅ and D≠∅. Inside an exchange the frozen cohort P(E) is the only wake set for no-mention messages (mention-pierces wake the mentioned agent without joining the cohort); deferred pairs get obligation-only `required_through_seq` raises (U0 — every conversational message raises `required` for EVERY recipient, separated from wake-mint eligibility) with no token and no wake audit. Closure = quiescence (no live cohort wake covering the exchange) + 5-minute grace, or the 4-hour reaper (fail-open), or suppression on abort/archive/removal/corruption — releasing every pair with unacknowledged content via exactly ONE slot-checked range-covering catch-up wake (`acknowledged+1..required`, contiguous, no fabricated acks). A pair whose live slot GENUINELY covers the released range (running `running_through >= required`, or any queued wake — its coverage is fixed at claim and `required` is monotonic) is coalesced (never a second token); a released deferral whose live slot does NOT cover it (a pre-arrival claim with immutable `running_through < required`) is durably marked `catchup_pending` and receives exactly ONE post-slot catch-up when the blocking wake reaches a TERMINAL outcome — failed/timeout settlement, or a recoverable restart terminalization — surviving daemon restart, never stranded (TASK-6057). Fail-closed recovery classifications (both-slots corruption, non-recoverable/invalid/malformed running or queued ownership) REVOKE the pending marker atomically in the same transaction and never mint a catch-up from state recovery declared untrustworthy (TASK-6065); the retained unacknowledged range is then served by the next conversational arrival (Phase-1 fail-open). Tightened D1: a wake claimed before a message never covers it (immutable claimed range + durable `started_at`/`enqueued_at` persisted in the settled audit payload `covered_from_seq/covered_through_seq/claimed_at/minted_at/exchange_held`). TASK_FOLLOWUP/BOOTSTRAP stay isolated (documented pierce source inside an exchange); daemon_restart is an interruption, never terminal, while a recovery replacement covering the exchange is queued/running (startup reconcile step 6e); corruption sweeps fail closed (suppress + diagnostic, never blind release). **Rollback is code-version rollback / operational containment only** — there is no user switch (TASK-6027): reverting the code stops new exchanges; an open epoch from a reverted deploy is closed by the normal quiescence/4h-fail-open bounds (never left stranded — the reaper, startup reconcile, and write seams evaluate closure idempotently). F1 measurement boundary `RATIFIED_EPOCH_F1` (Phase-2 epoch/window untouched; no historical backfill; any-burst deferral and the substantive-peer-engagement doctrine amendment remain separately gated — not implemented). Slice D's read-only release-measurement harness (`runtime/infrastructure/thread_release_measurement.py`, doc `docs/operations/gh-688-phase1-release-checklist.md` §8) reproduces the ratified Phase-2 acceptance metrics (mentioned-message saving 293/499→~24/204, founder coverage 698/0-loss, org-wide decline report-only) over the epoch `2026-08-26T14:25:23Z` with interim labeling until the one-month window completes. **Observation remains explicitly pending — do not claim analytics validation or a production result.** Do not revive the dead `thread_messages.addressed_to_json` (its separate cleanup plan is intact).
- **Prompt transport + thread provider-session lifecycle (THR-200).** The invocation prompt is delivered on **stdin** for claude (2.1.241), pi (0.84.2), codex, and opencode (1.18.25, TASK-6080 audit — pinned-version canaries: exact Unicode/newline semantics, `>=512 KiB` UTF-8 payloads); **Thread provider-session resume** is an optimization (never a correctness dependency; SQLite transcript canonical) for the executors whose contract is PROVEN against the installed CLI (TASK-5977, THR-200 seq 31): claude `--resume <id>`, codex `codex exec resume <thread_id> --json -` (same workspace-write sandbox + localhost network posture carried as `-c` config overrides since the resume subcommand has no `--sandbox` flag), pi `-p --mode json --session <id>` (fail-if-missing — never `--session-id`, which silently creates a fresh session), opencode `run -s <id> --dir <workspace> --format json` (stdin prompt; SAME project directory required — an opencode session is bound to its project dir and a different `--dir` hangs rather than failing fast; the thread workspace is stable per agent, and org relocation / workspace-path changes must invalidate stored opencode ids). All four re-emit the SAME stable id after non-interactive continuation and read the prompt via stdin (opencode large-prompt stdin verified live on 1.18.25: 606,099 bytes through a pipe). Registered custom-adapter profiles stay fresh-session — their resume contract is not part of the standard envelope (custom-CLI decision matrix, TASK-6080). Provider-declared session eviction transactionally combines the eviction audit and the durable `agent_session_id = NULL` invalidation BEFORE the full-prompt fallback; a failed fallback leaves the id NULL and the delivery watermark unadvanced. Eviction is classified ONLY from each provider's exact rc=1 stderr signature ANCHORED to the regex-escaped attempted session id (claude `No conversation found with session ID: <attempted-id>`; codex `Error: thread/resume: thread/resume failed: no rollout found for thread id <attempted-id> (code -32600)` — the observed complete line including the CLI envelope; pi `No session found matching '<attempted-id>'` — each verified 2026-08-28 to echo the attempted id verbatim, matched as the COMPLETE observed stderr line with only horizontal whitespace permitted) — or, for opencode 1.18.25 (TASK-6080 audit), rc=1 + EMPTY stdout + one complete LF/CRLF-delimited physical stderr line exactly `Error: Session not found` after ANSI-SGR stripping (the attempted id is NOT echoed; unrelated physical lines may coexist, but same-line prefix/suffix and cross-line assembly never match) — never from generic legacy substrings, cross-provider text, stdout-only text, wrong rc, wrong/missing id, prefix/suffix near-matches, a marker embedded in auth/quota/transport output, or ambiguous output, and never with more than one fresh retry. Archive, successful executor switch, and agent termination clear resume state (id NULL, watermark 0); each boundary is a database-owned transaction (reset + `thread_session_invalidated` audit atomic; termination reset+audit inside the terminate-cleanup transaction; archive wraps the status flip + resets + audit), a failed switch is rolled back so no new executor is installed, and a failed archive leaves the thread OPEN with every session row unmodified; participant removal deletes the row and its session state together (no redundant clear). The proven contracts do NOT require executor/model/config fingerprint invalidation, so no fingerprint column is added (EXCEPTION: an opencode session is bound to its project directory — resume is safe only for the identical workspace path, so org relocation / workspace-path changes must invalidate stored opencode thread session ids). The GH-688 claim gate stays strict (`<` resumes, `=`/`>` run the full prompt — no required sequence is ever omitted), and a resumed delta is authorized ONLY when the ENTIRE required post-watermark range is proven present and contiguous at the production seam (uncapped canonical load + independent authoritative max-seq query + sequence-completeness proof; truncated loads, internal holes, and claim ends beyond the transcript fail closed to the genuinely complete canonical full prompt), and a stored provider id whose durable delivery watermark is null/zero/negative (<= 0) is NEVER eligible for resume — it must make a fresh invocation with the complete canonical transcript; the equality state self-heals after one successfully settled full-prompt turn (no watermark-comparison change). TASK execution NEVER resumes provider sessions. THR-187/195 = evicted-id wedges; THR-198 = healthy-session equality wedge that needs only transport. See `docs/agent-guides/agent-executors-and-permissions.md`, `docs/agent-guides/orchestrator-contracts.md`, and `docs/agent-guides/features-and-invariants.md` (Thread Agent-Session Resume).
- The temporary containment roots above are workspace-owned and measurable; they are not cleanup-eligible.
- Packaged Python source is `runtime` and `cli`; `pyproject.toml` currently builds those packages. Do not treat top-level `src/` as canonical source unless tracked `.py` files and packaging/imports are updated.
- The daemon is FastAPI in `runtime/daemon/`; the CLI is a thin HTTP client in `cli/`; the SPA is in `web/` and builds to `web/dist/`.
- **Managed remote access N0/N1/N2 amendment (THR-097 seq360/seq374/seq379/seq392).** The managed transport value is `embedded`: packaged macOS tsnet/userspace WireGuard connects to the build/test-only Linux core in `app/linux/tsnet-sidecar/`, which proxies raw TCP only to the N2 Python connector's explicit `managed` listener on literal `127.0.0.1`. `runtime/remote_access/managed_provider.py` reuses the existing pairing and locked gateway pipeline; the connector alone owns application identity, credentials, epoch, allow-list/route policy and revocation and injects the daemon bearer only on the final `127.0.0.1:8765` hop. The N1 core fail-closes credential redemption/listener startup and removes external admission before active connections and engine state; persisted N2 revocation likewise stops the listener first, then seals/closes active flows, then performs downstream runtime cleanup, retaining failed cleanup handles for idempotent retry. Supervisor provider start, READY publication, persisted-revocation reconciliation, and shutdown share one re-entrant lifecycle boundary: either provider start completes first and stop follows it, or stop/revocation completes first and late start is refused/idempotent; partial start is cleaned without retained provider/registry state. Shutdown observably stops the listener, closes registered active flows, and drops cached registry/pairing runtime state exactly once, so repeated shutdown leaves no listener, active flow, or runtime residue. N2 adds no address selector or fallback and exposes readiness only after the existing connector gates pass and the loopback listener binds. The checksum-pinned N1 dependency/license artifacts are unchanged. N1/N2 are not package/composite-service integration, provisioning, deployment, distribution, or acceptance; N3–N6 remain gated. Neither endpoint requires system Tailscale; wildcard, LAN/plaintext, public-Tailscale, and public-DERP fallbacks remain forbidden. TASK-6298/TASK-6289 remains unchanged Supported-DIY, and Unit 4B-2 remains independent.
- **Managed remote access (normative contract, THR-097 merge unit A).** Fixed invariants: one Headscale cell per customer is the primary hostile-tenant boundary; the shared DERP fleet observes/relays only network metadata and WireGuard ciphertext and never grants reachability; the home connector is a **supervised portable Python companion** (never an in-process daemon listener) that enforces an explicit allow-by-method+normalized-template remote allow-list, current paired/current-device identity, and current policy/revocation epoch, failing closed; the daemon stays **loopback-only**; the daemon bearer is injected **only** by the connector on the final connector→127.0.0.1 hop and must never appear in remote input, client, Services, Headscale, DERP, network, fixture secrets, logs, errors, or audit. Revocation denies/closes before or atomically with node removal; never success early. Governing spec: `docs/superpowers/specs/2026-08-26-managed-remote-access-contract.md`; machine-readable contract + hostile threat fixtures: `tests/contract/managed_remote_access/` (consumed by the retained hostile lab; the former Python contract validator is retired). `route-classification.json` `included` (web coverage) is NOT remotely-allowed-equivalent; Swift `SurfaceAllowList` is legacy deny-list evidence, not authority. **Implementation status:** the portable supervised Python connector core (merge unit C / TASK-5724 phase unit 2) ships in `runtime/remote_access/` with a loopback-only harness and tests at `tests/remote_access/` — strict parser/canonical normalization, versioned route-policy consumer (locked nine-step decision order + every security-relevant nested value validated by exact canonical equality against the Unit-A semantics — contradictory non-empty prose and altered allowed-template lists rejected at load — plus operational state; schema/digest/version/staleness drift fails closed; unknown states never active), connector identity + device-proof verifier seam, current authorization/revocation with live-stream closure via one authoritative `RevocationCoordinator` transaction (the registry seals the retained stream wrapper fail closed before trust-state application — a raising transport close leaves the externally retained handle irrevocably rejecting receive/send, surfaced as `RevocationIncomplete`; the transaction is serialized across concurrent revocations and the stream registry shares/persists the complete cleanup terminal result, so no caller returns success while an in-flight or completed cleanup failure relevant to the sealed generation is unreported; old public `apply_revocation` bypass removed; admission and EVERY ownership/membership mutation — `open` admission, duplicate-id replacement, `close(stream_id)`, the retained-wrapper public `close`, and `close_all` — share ONE atomic lifecycle boundary: the lifecycle lock guards both the sealed flag and registry membership, every mutation seals the affected wrapper(s) atomically with its membership transition (no pop-without-seal ownership escape), any admission not fully registered before the seal fails closed and never returns a usable wrapper, transport open/close and duplicate-replacement callbacks never run under the lifecycle lock but remain INSIDE the revocation acknowledgement barrier (outside-lock transport closes that linearized before the seal are terminal before `close_all` success — revocation never reports success early), and same-thread re-entrant `close_all` from an unfinished transport cleanup callback MUST return fail-closed non-success (founder ruling THR-097 seq140): it may not exclude its own in-flight cleanup and publish success, may not mark the cleanup terminal with an incomplete failed-id set, and may not erase a callback failure that becomes terminal after the re-entrant call — the failure is persisted on the registry and re-surfaced by a later close_all/revoke (REV-007/REV-008), and derived `is_open`/`raise_if_open` are synchronized on the same lock; the gateway never double-closes a handle the registry owns), allow-list enforcement, remote-auth/hop-by-hop stripping, daemon-credential-provider seam, and a forwarder that targets only literal 127.0.0.1 and normalizes every forward/open/stream failure (connection refusal, timeouts, hostile exception text) into stable secret-free denials while deterministically closing partial resources; in-memory persistence abstraction only, no tailnet/external bind, no Headscale/DERP/Services integration, no packaging. **Linux supervised connector packaging (phase unit 3) is IMPLEMENTED** in the same package: an atomic, corruption-detecting, owner-only local trust-state store (``AtomicFileTrustStateStore`` satisfies the ``TrustStateStore`` protocol with a schema-agnostic non-normative envelope — atomic replace/fsync, sha256 corruption detection, symlink/loose-permission rejection, plus a founder-approved (THR-097 seq163) **non-database companion monotonic generation/digest anchor** at ``<state>.anchor`` outside the replaceable snapshot: the envelope generation must equal the anchored generation AND the anchor digest covers the exact snapshot bytes, so any previously valid OLDER snapshot replayed after a newer revocation/generation — including across a new store/process instance — is deterministically rejected, and missing/mismatched/corrupt/stale anchors, partial snapshot/anchor pairs, symlinked anchors, and loose anchor permissions all fail closed; a crash between the snapshot and anchor writes leaves a mismatched pair that load() rejects (the crash-consistency contract is documented in the module); NOT the founder-gated managed persistent schema — no DB, no migration), a ``SystemdCredentialProvider`` (``LoadCredential=`` injection — the service user never reads the daemon home), a five-gate readiness evaluator (``ConnectorReadiness``: daemon literal-127.0.0.1 reachability, credential permissions, current policy, bind identity, non-corrupt trust state — **no listener unless ALL pass**), a least-privilege systemd unit renderer (dedicated service user, empty capability bounding set/ambient capabilities, ``ProtectSystem=strict``, ``NoNewPrivileges``, ``LoadCredential=``, ``Type=notify``+``WatchdogSec``+``Restart=on-failure``; user-mode renders omit the directives the user manager cannot apply — ``PrivateDevices``/``ProtectKernelModules``/capability directives — verified on real systemd), an injectable ``SystemdServiceManager`` (install/uninstall/start/stop/restart/enable/disable/status + upgrade with auto-rollback and rollback), a **LAB-ONLY** conformance provider adapter (explicit ``lab_only`` + concrete lab bind address required; wildcard binds refused; runs the full gateway pipeline and forwards to literal loopback with bearer injection on the final hop; 403 category-only denials; never a product/Supported-DIY lane and NOT closure of THR-034), a ``ConnectorSupervisor`` (readiness-gated foreground loop with sd_notify READY/WATCHDOG/STOPPING — ``READY=1`` is emitted ONLY after the provider actually started and the listener is proven bound, never after a bind/start failure; expected operational listener failures — occupied bind port, permission, unavailable address — are normalized from the socket ``OSError`` to the documented ``LabProviderError`` category at the ``LabProviderAdapter.start()`` boundary (category-only message), so a REAL bind conflict keeps the loop retrying with ``STATUS=provider failed to start; no listener`` and no process exit (no systemd restart-throttle; the listener comes up once the conflict clears) while unexpected defects still propagate loudly; fail-closed listener stop on readiness loss; redacted ``diagnose``; ``install`` stages the service config/policy/state into the declared systemd-managed ``StateDirectory`` location accessible to the dedicated service user and the rendered unit points ``--config`` there — never at a ``~/.happyranch`` path the hardened unit cannot read; the source trust-state pair is only an INITIAL SEED when no managed pair exists — once a managed snapshot+anchor pair exists it is AUTHORITATIVE and reinstall never overwrites/rolls it back with a stale operator source pair (stale = refused, same-generation-different-bytes = refused, identical = no-op, strictly newer = adopted) and stages any advance transactionally (fail closed, no usable mixed pair); the service path automatically consumes ``$CREDENTIALS_DIRECTORY``/``LoadCredential=`` with no redundant config and no daemon-home token read; a provider-less ``run`` configuration is rejected at startup (fail closed, never ``READY=1``)), and the ``python -m runtime.remote_access.cli`` operator surface (run/install/uninstall/start/stop/restart/enable/disable/status/readiness/diagnose/upgrade/rollback; ``run`` requires ``--lab-only`` when a lab provider is configured and refuses to run with no provider at all). Real user-systemd conformance tests run under ``-m integration`` gated on an operational probe with explicit skip reasons: one launches the **ACTUAL rendered ``ConnectorSupervisor``** through the real ``Type=notify`` READY contract and proves the lab listener is bound, an end-to-end request reaches the literal-loopback daemon, restart, and stop-with-listener-gone (upgrade/rollback round-trip, enable/disable, daemon-reload also run against the real user manager); unit tests are deterministic with fakes. The hostile tenant-isolation harness (tranche unit B) ships at `labs/tenant_isolation/`. Provisioning/defaults/retirement remain unimplemented and outside any current PR; the capacity spike (tranche unit D) is deferred with no claim. **Supported-DIY customer-owned-network adapter (phase unit 3A) is IMPLEMENTED** (TASK-6032, fix-forward TASK-6039) as the PRODUCTION Supported-DIY lane in the same package: a customer-network address resolver/validator (`network.py` — the ENCRYPTED `tailscale ip -4` mode ONLY; the former plaintext explicit concrete-address mode is REMOVED and fails closed with a migration message, because bare HTTP on an arbitrary LAN/public interface would breach the fixed no-plaintext-service-path invariant — the customer-owned network is the customer's own WireGuard-encrypted Tailscale/headscale tailnet; wildcard/loopback/multicast/link-local/reserved/broadcast refused, fail closed), the local pairing ceremony engine (`pairing.py` — single-use expiring 8-char codes stored as digests only; redemption issues per-device `hrpair_` credentials at a MONOTONIC epoch, re-pairing invalidates old authority, and every ceremony mutation is SERIALIZED in-process AND across processes through the store's owner-only inter-process transaction (an fcntl.flock on `<state>.lock` making load, generation validation, snapshot publication, and anchor publication ONE serialized mutation boundary — the check-then-save race is closed, so concurrent redemption of one code from threads OR separate processes yields EXACTLY ONE credential, concurrent mutations are never lost, and the snapshot+anchor pair is always published consistently and loadable); revocation through the authoritative `RevocationCoordinator` closes live streams before the persisted epoch advance, per-device and all-devices revocation survive restart, `remove-device` REVOKES FIRST (streams close, epoch advances, persisted) and THEN deletes the record — a removed/lost device can never retain an open stream and the revocation survives restart — and removal denies identically to absent — no existence oracle), the production `DiyProviderAdapter` (`diy_provider.py` — binds ONLY the tailscale-resolved customer-owned-network address, serves the inherited THR-034 wire contract `POST /pair` + `X-HappyRanch-Device-Credential`, consumes/strips the credential at the boundary so it never reaches the daemon, runs the full locked gateway pipeline per request, forwards to literal 127.0.0.1 with bearer injection on the final hop), shared secret-free HTTP serving (`httpd.py` — the lab and diy adapters share one serving path; the lab provider is UNCHANGED in role and remains LAB-ONLY), additive OPTIONAL trust-state envelope fields (`credential_digest` on device records, `pending_pairings` in the payload — pre-3A envelopes still load; digests only, never raw credentials), `ConnectorConfig.diy` (mutually exclusive with lab; `run` requires the explicit `--diy` opt-in; provider-less run still rejected, never `READY=1`), a supervisor that owns ONE authoritative live-stream registry and ONE pairing manager per process shared by the gateway ctx factory, the provider, and the ceremony surface (in-process revocation closes the REAL shipping streams, never an unrelated empty registry), a cross-process revocation reconciliation in the `run` loop (a CLI `revoke`/`remove-device` in a separate process persists the epoch; the connector closes its live streams on the next loop pass bounded by `poll_seconds`, fail closed, and — because the registry is ONE-SHOT — ROTATES the authoritative runtime at a fail-closed lifecycle boundary the moment the registry is sealed: the listener is stopped first (no request/stream is admitted during the handoff), the cached registry and pairing manager are dropped, and the provider and every captured ctx-factory reference are rebuilt and restarted only when readiness passes, so a re-paired or unaffected device opens NEW streams in the same process lifetime and a revoked-with-no-live-streams revoke causes no listener gap), and the CLI ceremony surface (`pair`/`list-devices`/`revoke`/`remove-device`/`pairing-status`/`recovery --factory-reset` — truthful secret-free lifecycle states; `revoke` NEVER claims cross-process stream closure it cannot prove — it reports the persisted epoch and the connector's next-reconciliation closure; recovery deletes BOTH snapshot+anchor files per the store's crash-consistency contract). A REAL-network acceptance (`tests/remote_access/test_diy_acceptance.py`, `-m integration`) runs the away-client wire contract over a real network path to the real `cli run --diy` connector and a real loopback daemon — the customer-network address resolved through the REAL shipping tailscale-mode resolver path with a stub `tailscale ip -4` executable (no tailnet on the host) — proving allowed/forbidden routes, direct daemon/bearer attempts, restart-with-persisted-revocation, re-pair invalidation, replayed/removed credentials, outage fail-closed listener stop + supervised recovery, credential/token leakage scans, and (fix-forward) a GENUINE cross-process SSE negative: a live in-flight stream served by the connector process is closed by a `revoke` from a separate CLI process within one poll interval, and the CLI output carries no false success. The signed-macOS-binary and tailnet-transport hops remain honest residual gaps (this Linux host has no macOS build and no Tailscale client/network; THR-034 signed acceptance is NOT closed), and the macOS production app is unchanged by this PR.
**Daemon-managed workspace cleanup scheduler (THR-195).** Cleanup is a **system-default daemon capability**, independent of all user Schedules (founder ruling THR-195 seq 129; per-agent defaults TASK-6036). `runtime/daemon/workspace_cleanup_scheduler.py` — a sixth daemon-owned loop registered in `runtime/daemon/app.py` — measures EACH AGENT's own workspace on a bounded, fail-open budget (one wall-clock deadline shared across every git subprocess via `min(cap, remaining)` + post-call expiry checks; workspace/repo/worktree caps propagate truncation). A bounded timeout, error, or cap/truncation result that makes measurement unavailable bypasses only numeric threshold evaluation, so otherwise-due spawning continues with honest unavailable advisory context; only an available numeric result below 1 GiB skips. Per agent, when the latest due daily local 03:30 occurrence (org-tz; existing occurrences delimit half-open windows compared as UTC instants, nonexistent spring-forward times are skipped and ambiguous fall-back times use `fold=0`) is unserviced — no marker at/after it, including legacy weekly rows — and no prior cleanup task of that agent anywhere in the complete history is non-terminal, the daemon triggers an ordinary root task ASSIGNED TO THAT OWNING AGENT with a daemon-composed brief carrying the fresh advisory snapshot — never through the repository-refresh prompt seam, never touching `schedules`. The first TWO triggered runs per agent are STRICTLY report-only; from run #3 the brief is the approved TASK-5552 §4 cleanup contract (bounded, Git-aware, non-force, action-time-re-derived eligibility; the advisory block itself never authorizes removal). Both the daemon daily marker and manual dispatch use the ONE shared `workspace-cleanup` TASK system contract (`requires_repo=false`); the exact manual first line is `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)` and an unmarked manual request is inventory-only. Its bundled read-only `runtime/skills/bundled/workspace-cleanup/scripts/check_path_use.py` implements the approved THR-259 seq171/seq185 observation: an authoritative recorded terminal status plus a fresh complete same-user process scan replaces separate live-session/task-to-process identity; the fixed login/session daemons (sshd-session, systemd --user, (sd-pam), ssh-agent, gpg-agent, gcr-ssh-agent) qualify only by exact readable process name AND exact bounded cgroup role and are deliberately uninspected, while every other unreadable same-user process is unknown and skips. That is a snapshot with accepted later-opener/data-loss residual, never proof of OS-wide absence or executable identity. The shared procedure runs the scanner only through a task-bound host-visible HappyRanch job and accepts only an exact fresh matching receipt, with no direct fallback; candidate-specific task and trigger joins exhaust their keyset pages and fail closed on missing, changing, conflicting, malformed, or incomplete relevant evidence. A clean candidate may prove preservation through the accepted durable ref, an owning origin task branch whose head equals or descends from the candidate, an owning-task merged PR, or a containing any-task merged PR. The any-task route requires complete stable double-read discovery, merged/default-branch confirmation, and an independent complete stable compare; discovery alone and other-task unmerged PRs never count, while owning-branch unmerged evidence still refuses. Merged integration may not preserve original commit topology. Whole dirty worktrees remain protected; only a literal root `.venv` or `node_modules` cache may be removed after every cache gate and the 24-hour floor while tracked source bytes and Git status remain unchanged. Advisory content is aggregate-only and prominently ADVISORY / STALE ON ARRIVAL / not eligibility/candidate/safe-removal guidance; no path is enumerated; no pending jobs or `blocked_on_job_ids` are used as liveness. Routine scheduled and exact-marker manual cleanup reports remain durable on the existing agent page through normal task completion/results (THR-259 seq418). The scheduler creates no cleanup-report thread and workers have no routine thread posting or reuse obligation. Preserve all historical threads, messages, associations, results and audits; explicit founder-requested coordination, truthful anomalies, partial failures, unknown bytes, independent verification gaps and required current-session final report-completion remain. Kill switch: `workspace_cleanup.enabled` in the org `config.yaml` (default true). Docs: `docs/agent-guides/agent-executors-and-permissions.md`, `docs/agent-guides/orchestrator-contracts.md` §Daemon-managed workspace cleanup scheduler. First-two bookkeeping and the exact ordinal persist via existing durable mechanisms only (daemon-marked task rows via the complete read-only `Database.summarize_workspace_cleanup_marker_history` — bounded rowid keyset pages computing the exact all-history count, newest UTC instant and any-unfinished flag with no logical cutoff; lookup failures fail closed for triggering). After awaited measurement, task ID allocation, brief composition and ordinary task insertion run synchronously under existing `org.db_lock`, with no reporting-thread dependency; failed insertion is audited and never enqueued; workspaces are enumerated in bounded batches so no agent is starved; no schema/API/CLI/auth change. Disclosed unchanged limitations (daily cadence does not cure them): the selector's 1001-row history refusal and sixth-raw-terminal-row whole-batch refusal and the selector's bytewise-order refusal.

**Cleanup local evidence admission (THR307seq4).** The local evidence ordering is attempt envelope → fresh native gates → canonical
eligibility → first mutation → native terminal outcome → verified fsynced journal
append → canonical reference-only acknowledgement → whole-coverage admission.
The batch journal is the sole ordered outcome ledger. Direct Bash use needs the
same bound manifest/history/journal/evidence sink; stdout alone grants no action.
Cache isolation-directory creation is mutation. Failed rename/rmdir causes and
separate original/cache/directory identity and measurements survive; only positive
restoration/absence/source/status/raw-index proof admits safe refusal. Residue or
uncertainty halts. Git read observations suppress optional index refresh.
Each attempt and acknowledgement uses the existing exclusive pending write,
flush/fsync/close and exclusive hard-link publisher. A canonical file with its
identical expected staging sibling is valid; pending alone, conflict or collision
is not. Acknowledgements reference binding, envelope/eligibility identities and
exact journal prefix/row range/hash/ordinal, without a second success ledger.
Batch resume and report preflight the entire bounded namespace and journal before
any skip/admission; orphan or unacknowledged terminal evidence blocks, including
when fallback is missing/truncated. Explicit native failure/interruption wins over
readable files. Same-batch acknowledged prefix progress and later enclosing native
batch/sequence/postcheck completeness are separate prerequisites. No automatic
recovery or directory-fsync/crash-atomic guarantee follows. Historical bytes remain
historical, with missing new-contract proof explicit; never rebind or retry them.

Captured native uncertainty (output cap, timeout, spawn/capture failure, signal or
failed publication) is distinct from a fully captured ordinary nonzero gate
denial. The writer and every eligibility/coverage/resume/report reader retain
the native cause and block action or next admission, even with healthy enclosing
files or missing fallback. Native action/capture children inherit the candidate
cancellation group. The batch stops and reconciles its owned group before
terminal observations; outer capture allows that cleanup before forced kill.
Residual or unproven survival is unknown and unsafe, never clean completion or
permission to retry. Possible mutation remains partial/unknown without reuse.


**Owning-branch cleanup authority.** For daemon-managed workspace cleanup, an existing owning origin task branch is authoritative: if it does not contain the candidate commit, or its containment evidence fails, cleanup refuses without falling through to owning-task or any-task merged-PR proofs.

The current-use scanner treats a parseable leader `Z` as evidence for that task
only. It independently enumerates the bounded group frontier before requiring
leader namespaces or references. Repeated PID/TID, Tgid, starttime and complete
UID identity with repeated `Z` may omit only that task's dead references; live
workers still require their own `cwd`, `root`, `exe`, `maps`, private fd table,
mount/user namespaces and path resolution through their own proc root. Missing
worker census after leader exit remains `unknown`; `Threads: 1`, a controller's
TIDs or a guessed singleton never substitute for a complete census. Canonical
positive ASCII TIDs, uniqueness, actual group membership and unique complete
Pid/Tgid/Uid/State status fields are required. Malformed identities, reuse or
credential changes refuse; ordinary live R/S transitions alone are not reuse.
Thread membership/count observations converge within the existing pass cap
and shared deadline, reserving final census and identity/view checks. Known
positive use remains `blocked` even with incomplete evidence. Public result,
coverage and row schemas, scan/Bounds compatibility and exits 0/3/2 are retained.

For receipts from this revised source, `scanned` counts distinct admitted
non-exempt group leaders; `exited` counts distinct confirmed terminal task
identities (Tgid,TID,starttime,all four UIDs), with initially absent unbracketed
PID census slots accounted separately; `vanished` counts deduplicated actual
absence/closed-reference observations, including separately observed reopened
reference incarnations. A present confirmed Z contributes one exited and zero
vanished; leader/task-leader aliases and rediscovery do not recount it. These
counters are different units and must not be summed as population proof.
Historical receipts retain their original source-bound event meanings and
bytes. This correction grants no cleanup, package refresh, deployment or
live-process unreadability waiver.

**Forward-only root-terminal worktree reconciliation (THR-259 seq476 adopting474).** Each original newly committed structural root in `completed`, `failed`, `cancelled` or `superseded`, including NULL-parent workflow tasks, reconciles only its parent ancestry after existing history/teardown and outside owner locks. Recorded child `worktree-deferred:` evidence becomes advisory at this root pass without rewriting results/history. Child hooks retain their one-task/five-second deferral policy; manual/daily retain age/ordinal/peer/history/host-job/cache authority. The shared bundled safety owner proves fresh exact-HEAD preservation with live owning-branch precedence, recursive no-follow boundaries, unchanged same-user scanner and action-time registration/ownership/protected identities. Dirty/local-only/open/active/held/successor-used/unknown paths retain; terminality is not quiescence. Existing append-only audit start INTEGER id plus root id/created_at identify a pass; mutable timestamps, replay and missing finish never create a retry. One owner batch shares 10s work including 0.5s DB/50ms lock admissions,1s report,1s owned-group reap;16 events/256 nodes/depth64/page64/16 attempts/5s clipped candidates/200000 entries/1MiB capture/256KiB receipts. The 12s ordinary-I/O target cannot interrupt SQLite/fsync/kernel I/O. Incomplete membership/protection starts zero removal and names exact frontier/remainder; possible post-action or report anomaly halts. Success requires absence, unregistration, branch preservation and protected identities. No force, cache-only action, task-tmp action, new schedule/queue/retry, schema/auth/permission change, historical sweep or deployment follows. See [the full contract](docs/agent-guides/orchestrator-contracts.md#forward-only-terminal-task-worktrees).

**Temporary-filesystem containment and inode advisory (THR-200/TASK-6501).** The workspace-cleanup advisory adds fail-open `statvfs` inode used/free/total/percent/threshold fields and a full-CI preflight. These observations are non-authoritative: they never grant cleanup authority, enumerate candidates, or cause the scheduler to inspect, move, quarantine, restore, unlink, or recursively delete `/tmp` content. Every runtime-launched task-agent and job subprocess now receives `TMPDIR`/`TMP`/`TEMP` rooted at the canonical mode-0700 `<workspace>/.happyranch/task-tmp/TASK-N`; runtime-owned, bounded, atomic v1 observations live separately at `.happyranch/task-scratch-manifests/TASK-N.json` and distinguish regenerable scratch roots from durable/recovery manifest evidence. Containment-variable or sidecar changes between preparation and launch fail closed; unrelated inherited environment is preserved. Manifests are report-only evidence and never deletion authority: automatic teardown, periodic eligibility expansion, backlog cleanup, and manifest-driven removal are not implemented. Executor language/package caches remain below `.happyranch/cache`, registered scratch worktrees remain below `.happyranch/scratch/worktrees`, and small daemon callback payload contracts are unchanged.
- **Usage v1 lifecycle attribution and reply linkage (THR-272 PR2/PR2b).** Invocation-time cohort identity is additive and never inferred from current agent configuration after the fact. `thread_invocations.executor` and `.model` are nullable TEXT populated with `started_at`; legacy rows remain NULL. `thread_invocations.reply_message_seq` is a nullable INTEGER set atomically with a reply message only when that transaction terminalizes the same thread/agent wake; a partial unique `(thread_id, reply_message_seq)` index enforces one wake per persisted message. NULL means the reply outcome was not recorded (legacy or no persisted message), not “no reply”; there is no inference or backfill. Task `session_start` retains `workspace` and also records runtime `session_id`, actual spawn purpose (`worker_execution`, `manager_decision`, or `unattributed`), executor, and nullable model. Decision versus worker purpose follows the immutable spawn mode (`task` versus `subtask`), not role, ownership, or root/leaf shape; THR-247 callback-only recovery sessions are `unattributed` and remain durably identifiable through `task_completion_recoveries.recovery_session_id`. `dream_started` records executor and nullable model. A missing configured model remains SQL/JSON null, never the guessed literal `default`; no historical backfill occurs. Current contract: `docs/superpowers/specs/2026-09-30-usage-v1-design.md`.
- **Usage v1 read model (THR-272 PR3).** Bearer-authenticated `GET /usage/workload` and `GET /usage/efficiency` compute adjacent rolling 168-hour windows from lifecycle facts before optional usage correlation. Workload emits only agents active in the current window; comparison never fabricates previous-only rows. Replies count only consumed REPLY wakes with non-NULL `reply_message_seq`, disclose recorded/total-consumed outcome coverage, and withhold the Replies delta if either window contains an unknown outcome. Task usage joins by task/agent/runtime session, thread usage by thread/agent/runtime session (invocation-token fallback), and dream usage by dream ID/agent—never provider session ID. Missing or ambiguous usage remains in denominators; normalization alone defines coverage and reported token observations. NULL model is the distinct unpinned cohort, recovery stays outside Efficiency rows, and server-computed deltas enforce row-level 95%/unattributed suppression plus metric-level baseline withholding. No DDL or writer behavior is involved. Current contract: `docs/superpowers/specs/2026-09-30-usage-v1-design.md`.

- **Metrics persistence** (THR-066): daemon-global `metrics.db` at `<runtime_root>/metrics.db` (NOT per-org). Append-only snapshots every ~60s, 30-day retention. The store is `runtime/daemon/metrics_store.py`; the periodic writer piggybacks `work_hours_scheduler_loop`. **Routes**: `GET /api/v1/metrics` (live snapshot + pull-gauges), `GET /api/v1/metrics/history` (persisted rows, newest-first, bearer-authed, `?since=&until=&limit=` query params). HTTP latency is labelled by the matched FastAPI **route template** (e.g. `POST /api/v1/orgs/{slug}/tasks/{task_id}/completion`), never the literal `request.url.path`; an unmatched request records `METHOD __unmatched__` and a handler exception records `METHOD __error__` (elapsed time still recorded, exception re-raised). Snapshots carry an additive `format_version` marker (`2` = template labels; a stored row without it is legacy raw-path format and stays readable). Each persist cycle logs non-sensitive storage telemetry (route-label cardinality, serialized bytes, row/prune counts, oldest/newest capture, DB/WAL bytes, page/free-list counts). Never delete `metrics.db`, `-wal`, or `-shm` manually. **Offline maintenance (startup-only, TASK-5443 replacement):** `python -m runtime.daemon --maintenance` (or `scripts/daemon.sh maintenance`) is an explicit one-shot that runs BEFORE the daemon binds an HTTP listener or starts its lifespan/schedulers/workers and then exits — it never serves traffic and never coexists with a running daemon. It runs the ordered MetricsStore sequence (strict-before prune at the unchanged 30-day cutoff → WAL checkpoint → `PRAGMA integrity_check` must be exactly `ok` → controlled `VACUUM` → post-VACUUM WAL checkpoint → post-vacuum integrity evidence), logs bounded before/after telemetry (DB/WAL bytes, rows, cutoff, page/free-list counts, duration, pre-/post-vacuum checkpoint and integrity outcomes, prune count, snapshot-size and route-label cardinality — never raw IDs/labels/content), and returns 0 on success / nonzero on fail-closed failure with a bounded, redacted classification (stable code or `operational-error`) plus fixed recovery guidance — never raw exception text, tracebacks, filesystem paths, or injected content — and no automatic retry (a fresh explicit invocation is required). Run it only while the daemon is stopped; it refuses when a daemon pid is alive and SQLite fail-closes (checkpoint busy / VACUUM locked) if a live holder exists. There is deliberately no live maintenance route, gate, or scheduler.
- Runtime containers are schema v2 multi-org trees: `<runtime>/orgs/<slug>/...`. Per-org routes are under `/api/v1/orgs/<slug>/...`; container-level routes are under `/api/v1/runtime` and `/api/v1/orgs`.
- Settings come from `HAPPYRANCH_` env vars, then `~/.happyranch/config.yaml`, then code defaults. There is no `.env` support.
- Agent names are plain strings discovered from `<runtime>/orgs/<slug>/org/agents/*.md`; do not introduce static agent enums.
- The task/subtask model distinguishes task owners (`task_type='task'`, produce `decision` blocks) from delegated subtask agents (`task_type='subtask'`, produce plain completions). Prose uses "task owner" and "subtask agent" over legacy "team manager"/"worker" language.
- Only root tasks (`parent_task_id is None`) escalate to the founder; non-root tasks fail and hand back to their parent (bounded recovery carries it up).
- Manager supersession creates a successor only for an eligible, still-current root. A still-current non-root manager task that returns `supersede` atomically fails with a `manager supersede refused: non-root task ...` note and wakes its delegated parent through the ordinary FAILED terminal tail; it creates neither a successor nor a `manager_supersessions` row. A cancelled or replaced claim has no failure or wake, and unrelated root refusal remains a no-op.
- **THR-277 authority-v2 refusal routing.** A still-current root v2 refusal retains the existing `escalated` transition and `escalation` audit. A still-current non-root refusal instead atomically becomes `failed` with `completed_at`, cleared chain/fanout state, note `authority_v2_refusal:<code>`, and the closed `authority_v2_refusal_task_failed` audit; it never writes `escalation`, never notifies the founder, and wakes its parent through the ordinary/recovery-owned FAILED tail. At startup, only `admitted` attempts are classified from their exact causal completion: blocked or parsed non-escalate results are left wholly untouched and unfenced, while missing/unreadable evidence, parser-unavailable callers, later stages, and parsed escalation retain refusal handling. All general escalation CAS writers include a root predicate and loudly refuse non-roots.
- **THR-181 pre-escalation authority hook (Track A):** before a current manager-owned Engineering root escalates, including a thread-originated root, exactly one audited LLM evaluation runs against immutable release policy. Every committed post-activation escalation has one `authority_hook` denominator outcome; runtime-raised mechanical escalations atomically record linked `not_applicable` rather than invoking evaluation. Thread id/origin remain structured provenance and do not relax same-root identity or the prohibition on revisit/successor/supersession/fresh-root replacement. A clean CONTINUE_SAME_ROOT atomically returns the same root to pending and mints a single-use lifecycle envelope bound to evaluation, causal result, manager, team, root, session, and policy identity. The next turn uses the manager agent's ordinary configured executor permissions and normal manager-decision validation; no exact-action whitelist, executor capability refusal, Claude allowTools narrowing, or opencode permission-map swap applies. The daemon remains authoritative for same-root identity, cancellation, replay, CAS, budgets, protected boundaries, and terminal audit reconciliation. Evaluator error or ambiguity fails closed to ESCALATE. See `docs/agent-guides/orchestrator-contracts.md` §THR-181.
- **Escalation continuation:** autonomous thread escalation continuation is retired. `orchestration_step_count` is monotonic telemetry, not a blocker; every genuine human blocker — including exhausted revise-round or per-slice retry budgets — remains escalated. The direct task-level continue route remains the named, auditable manual break-glass exception under the current shared-bearer trust model.
- **Escalation reason display (THR-279):** `tasks.note`, the authority-v2 refusal finalizer, escalation audit payloads, and `audit_log.task_id` scope semantics remain unchanged. Task detail, dashboard Waiting on you, and `happyranch details` receive an additive read-only `escalation_reason`: consecutive exact-task escalation audit IDs delimit the current episode; only an intervening manager `decision.action=escalate` can supply a v2 refusal's primary reason. Closed refusal codes map to server-owned plain-English secondary copy; a no-escalate episode has no primary, unknown codes render verbatim, ordinary escalations retain their existing bytes, and non-escalated tasks are unaffected.
- **THR-225 task-followup replacement bound:** an authoritative pending
  `TASK_FOLLOWUP` owned by the dispatching team manager may transactionally
  create one true replacement root for its causal thread-dispatched root. The
  budget is persisted per lineage, never prompt-derived or per-turn; the
  replacement root and every supported descendant/revisit/chain/fanout
  followup are permanently fenced. Manager supersession is a separate path
  that rejects thread-originated roots and is not a THR-225 lineage shape.
  Ordinary reply/bootstrap dispatch is
  unchanged; queue notification occurs only after the atomic task + invocation
  marker + system-message + audit commit. No task column changes meaning.
- **THR-207 capacity:** restart-required daemon defaults are 6 task workers and a healthy enforcement-capable global admission cap of 13 (6 task + 4 thread + dream + wake + schedule); macOS/no-enforcement remains conservatively capped at 4. The paired rollback is 4/11 plus restart.
- **Host-resource concurrency (THR-207 / TASK-5584 Slice A + TASK-5637 Slice B):** load-bearing ordering invariants remain governing: no agent subprocess launches before a daemon-wide admission lease; every terminal path (success, nonzero, timeout, cancellation, retry, shutdown, and setup failure) finishes containment and reconciles residue before releasing that lease; queued cancellation never launches and active cancellation uses the opaque containment handle rather than a bare PID. Ownership transfers at admission grant, carries the durable first-wins terminal reason, is generation-owned by session id, and is drained through the same registry, so an old generation cannot clear a newer PID/control and shutdown cannot lose an admitted attempt. The real task producer and both executor launch bodies use the capability backend; `SessionTracker` PID data is diagnostic/restart evidence while cancellation/cleanup controls are authoritative. Quiescence is fail closed: unreadable or errored containment evidence is unknown, not clean; Linux receipts retain truthful per-counter provenance and macOS retains honest capped process-group/census residuals. The healthy Linux backend applies and verifies the fixed resource limits while unsupported/degraded platforms state unavailable enforcement honestly. Bounded receipt provenance is published only through the existing operator surfaces. Governing implementation and the full platform-specific contract remain `runtime/orchestrator/host_supervisor.py`, `runtime/platform/session_backend.py`, and `docs/superpowers/specs/2026-08-24-host-resource-concurrency.md`.
- **THR-247 bounded Codex completion recovery:** the one-shot task recovery passes the existing host supervisor's per-invocation `allow_retries=False` control. A recovery 429 is therefore finalized, receipted, cleaned up, and released once with its honest `RATE_LIMITED` result — it performs no supervisor backoff or re-admission. The default remains retry-enabled for every ordinary task/thread/schedule/wake/dream invocation; global retry counts, schedule, admission policy, cancellation reasons, and result diagnostics are unchanged. The honest-passthrough fallback also passes its existing empty executor throttle backoff so there is still only one provider execution. Accepted consumed receipts settle only their captured running jobs; the parent/chain handoff is rechecked under the recovery owner predicate, while a thread followup is deliberately afterward and is not protected from a later replacement owner.
- Agents should perform side effects through the `happyranch` CLI. Baseline allow rule for every agent is `happyranch`.
- Agent-side completion and callback payloads must be single-line `happyranch ... --from-file <path>` invocations; shell separators and multiline continuations break permission matching. **The --from-file path MUST be absolute** (e.g. `/tmp/completion.json`), never relative — a relative path resolves against the agent's cwd and can litter stray files under the runtime orgs root.

**Thread reply provider breaker (THR-200 PR B).** One durable continuity per `(thread, agent, executor/model/policy)` opens after exactly three consecutive structured final provider-execution failures. Threshold 3 and cooldown 900 seconds are fixed policy constants, not environment/config settings, so restart cannot fork continuity or bypass an OPEN episode. Only a process proven launched and ending as provider nonzero, provider timeout, or post-launch contract failure qualifies; pre-launch/admission/host refusal, interruption/cancellation, canonical transcript gap fallback, exact eviction itself, successful eviction fallback, and a successful no-callback nudge do not. A failed eviction fallback counts exactly once. OPEN retains/coalesces the delivery range and launches nothing. After exactly 900 seconds the daemon scheduler atomically mints one HALF_OPEN probe; every tick also republishes a committed pending probe token through a process queue whose deduplication ownership spans queued and in-flight delivery. Worker success acknowledges that ownership, while a pre-claim exception or cancellation releases it for a later tick; the durable claim CAS preserves exactly-once provider launch. A recovered ownerless gap with no episode follows the same cooldown-probe path, never ordinary CLOSED admission. Held deferrals in an OPEN exchange remain held and excluded even when the breaker is otherwise recoverable. Probe success settles and closes; probe failure rearms 900 seconds. Episode/receipt/audit writes are idempotent and redacted; stale callbacks cannot close newer continuity. No API/OpenAPI/web projection or manual action is added by PR B.

**Failure diagnostics (THR-220).** Failed thread-invocation audits retain capped raw stdout/stderr tails as additive payload keys. Task, thread, and dream reports select one bounded human cause from complete stderr before tailing; exact known benign launcher/trust lines do not win, while meaningful stderr (including lookalikes) does. The proven API-error/session-limit envelope separately retains its bounded reset notice on those existing surfaces. This is not a comprehensive redaction guarantee and does not alter raw classifier, rate-limit, exact-eviction, retry-owner, or breaker inputs. Claude's session-limit notice is terminal but is not a short-backoff rate-limit retry signal.

**Cleanup activity performance (issue1019 / THR-295).** The supported schema
path adds three nonunique indexes after legacy columns exist: tasks(assigned_agent,
created_at DESC,id DESC), audit_log(task_id,agent) where
action='workspace_cleanup_triggered', and task_results(task_id,agent,id DESC).
`CREATE INDEX IF NOT EXISTS` retains existing names without validating definitions.
The reader SQL is unchanged. Breaker delivery listing and mint use sequential
`await asyncio.to_thread` calls with the original DB RLock. Synchronized close
serializes connection access; removal can cause a logged sweep error. Cancellation
can leave a worker running or a committed pending token for later recovery.
There is no added orgs_lock hold, worker drain or lifespan guarantee.

## Private D operation source — THR296seq135 / TASK11345

D now supplies the private native operation core in `team_move_operation.py`,
with pure canonical/context planning, one two-consumer profile interval and one
operation-owned publication fence. E now mounts the founder service/CLI under
the existing admitted human boundary. F proposes source enablement subject to
actual native readiness; independent whole-feature acceptance remains required.
A provenance model, agent bearer or caller success flag grants no authority.
Earlier B/C/D checkpoint absence statements describe those historical units.

The planner uses native AgentDef/TeamsRegistry validation and instruction/skill
selectors, without materializing, creating an ArtifactStore root, building a
package or emitting an event. It pins the three exact before/after images,
original file/workspace identities and modes, current names, complete selected
policy records, profiles/dependencies and generated permission/context/skill
manifests. It preserves unspecified YAML bytes and omitted Engineering routing;
only the approved two workers, exact head charter and empty Consultant retirement
are changed. Unsupported topology/metadata, explicit grants or new selected skill
packages refuse. Native permission algorithms and Codex enforcement stay intact.

WAITING reserves before capturing only actual running thread attempts. The final
census joins C1 task/Pause/workflow, C2 thread and C3 producer/writer/remote owners
with actual host ownership/residue, outside short gate/DB leaves. Native writer
epoch and DB change observations renew at the decision/release leaf. Hard pending
work or unknown cleanup remains blocking; no timeout, missing tracker, expired
lease or later cleanup clears sticky uncertainty. Profile leases precede the
publisher lock; no gate/DB transaction spans filesystem work or await. One native
batch handles both unchanged executor consumers, including empty dependencies.

The immutable decision binds one publisher invocation. Native fence discovery
recovers uncertain saves by that exact invocation, never another journal or an
old ready pointer. Audited per-agent resets derive founder attribution only from
the stored admitted service provenance and recognize the exact scoped audit plus
cleared post-state. A zero-row step invents no audit. Complete candidate validation
precedes every replacement; each exact owned before/after image is read back,
file/parent-fsynced and CAS-recorded. Canonical readers capture coherently or defer
through the short window; the shared TeamsRegistry object is updated in place.
Original workspaces use native instruction/settings, all-context skill package/link
and integrity owners without cloning, refresh, memory recreation or deletion.
Partial/omitted native effects hold repair; no unrelated shared-store rollback.

Private COMPLETE requires actual canonical/context/reset/selection/profile readback,
settled causal/process/writer facts, and the SAME journal `cache_installed` with
matching ready pointer/file/cache/digest and current inputs. The DB-only leaf saves
the original receipt and releases atomically. General store transitions still
refuse caller-controlled COMPLETE. Terminal replay returns saved state without
reset/materialization/publication. Explicit recovery requires the same target,
CAS and native original-owner finish or actual process-identity loss; no TTL or
supplied quiescence Boolean authorizes takeover. Held obligations redeliver through
existing deduplicating producers; armed schedules retain normal missed-slot rules.

Schema-owner discovery precedes Database compatibility writers, membership,
naming/cold publication and producer recovery. Without complete native proof,
affected orgs stay diagnostic-only; unrelated orgs may attach. A restricted recovery
handle validates the existing schema and exact images without bootstrap/migrations,
normal attachment or producer admission. Diagnostic orgs also fence global profile
reconciliation/writers so their dependencies cannot disappear from the closure.
Runtime registration/switch refuses to detach a hold. Updated shell and native Mac
launchers pass `--reader-guarded-start=online-team-move@3`: supported older argparse readers reject
this protocol before startup, including the earlier D Boolean-flag reader.
Updated launchers require a matching reader; direct
old interpreters, already-running old daemons and obsolete launchers remain outside
this cooperative guard. Source rollback cannot undo a durable decision. No actual
activation/restart/downgrade experiment is authorized or performed.

D correction: the existing operation audit owner records a closed version1
staging protocol with the RESET-to-CANONICAL transition, then an exact
operation/path/stage intent BEFORE exclusive allocation, acquired file and parent
identities BEFORE writing, and append-only terminal disposition AFTER file/parent
fsync. Recovery reconciles only those exact artifacts. A present path without
acquired identity stays actionable repair_required; it is never adopted, globbed
or deleted. Failed cleanup stays durable through owner-finished and recovery.
COMPLETE requires all stages disposed, exact replaced-inode coverage and fresh
absence readback. ONE-table SQL, old Progress serializers, historical audits and
saved terminal receipts remain unchanged. Legacy canonical-stage operations
without this protocol remain readable but held for missing ownership evidence;
no scan, inferred cleanup or historical rewrite can upgrade that evidence.

All per-org acquisition, strict resolution, filesystem and SQLite-open/read/close
observations normalize to bounded native diagnostics, preserving errno/SQLite
classification and original exception causes. Only genuine first-observed DB
absence is fresh. Raced disappearance or unknown observation remains diagnostic;
startup and runtime-switch callers retain earlier diagnostics through subsequent
reads and state adoption. Previously unavailable diagnostic/recovery owners
require an existing DB. Compatible readers leave only the affected org unattached;
incompatible readers and source-runtime detach fail closed.
Restricted recovery preserves the original error if handle close also fails;
standalone close failures retain native classification and never prove release.

The shared profile checker performs no diagnostic path/DB acquisition under a
profile lease. Ordinary global, rebind, claim/retry and synchronization writers
refuse `profile_team_move_diagnostic_org_held` without clearing the retained root;
EMPTY dependency sets still check diagnostics before yielding or mutating.
Adapter removal translates only `ProfileCoordinatorError` from its coordinator
span to HTTP409 with `detail.code`, including the retained
`profile_team_move_diagnostic_org_held` refusal for EMPTY dependencies. Unrelated
exceptions keep their existing handling; removal, audit and compensation stay
with the native adapter-store owner.
Positive reinspection belongs to native startup/attachment outside leases.
`add_org` requires the existing DB for a previously unavailable org and loads it
through strict schema/history and canonical readers. Its matching attachment
segment revalidates the actual already-open DB owner under the profile lease;
a current hold or invalid history refuses. Only successful synchronization and
shared-map insertion permit the existing post-attachment diagnostic removal.
Failed attachment and other diagnostic roots stay held. D's matching own-operation
exception likewise uses its opened native owner plus actual ContextVar identity,
including EMPTY dependencies; neither exception performs filesystem discovery.
Public recovery/reattachment is wired by E; F gates it on actual native owners.

The one batch interval (including empty dependencies) captures AgentDefs, complete
profile/dependency closure, candidate, current inputs and census outside durable
mutation leases. Short segments revalidate actual operation revision, native
roster journal/pointer, dependency mirror, writer epoch and both local-write and
SQLite data_version observations. Only bounded exact-image mutation runs within
the canonical/profile leases. Publication capture and final discovery run after
the leases release. The final DB-only leaf revalidates the original capture from
BEFORE discovery as well as the completed census, closing intervening-writer gaps.
These are source predicates; no crash, cleanup, race or recovery proof was run.

Acceptance is SOURCE/POLICY/STATIC ONLY on the SAME thirteen four-answer
observations. Every absent behavior/migration/recovery/native callback/permission/
process-loss/launcher/live-move result remains UNEXECUTED; general integration
INCLUDING callback SKIPPED THR243seq42; Python units RETIRED. No new cases,
parameters, fixtures, replacement campaign or execution allocation follows.

## Founder-managed teams (THR296)

Founder THR296 seq135 approves the full seq130 online plan under NEW root
TASK11345: ONE additive org-owned team_move_operations table, affected-agent
admission/deferral, forward recovery and startup/old-reader contract. A is
satisfied. B provides the optional durable store/schema/reference contract. C1
binds task allocation/claim, preparation and final Pause/host exclusion, including
causal task/workflow ownership and permanent-ID retry metadata. Preparation lives
through callback consumption and actual cleanup tails; unknown quiescence or
failed callback cleanup remains reserved. Binding/root Pause -> move gate -> DB
transaction -> terminal leaf; existing nested graph writers check durable hold
under DB without acquiring move gate beneath it. Terminal settlement uses DB only.
Move admission deferral propagates through the inner launch handler to the existing
pending-work owner without task failure, parent wake, followup or callback consumption.
Malformed installed state retains its actionable admission error. Direct 429 and
supervised passthrough retries preserve each returned uncertain attempt as a separate
Pause journal/reservation blocker; the next actual attempt keeps the original retry
budget, session and enqueue age and rechecks durable admission before FIFO/commit.
Provider return never proves containment. Durable Pause attribution remains through
workflow reconciliation and the actual outer cleanup batch. Its explicit disposition
distinguishes safe no-action/preservation from halted, unreaped, action/report failure
or unknown teardown; uncertainty retains the exact task/session owner and cleanup
facts, without changing the normal terminal result or expanding cleanup authority.
Retry attribution uses the existing entry id, task/agent/SID, owner identity,
fingerprint, generation and retry fields; the retained reservation adopts that
entry id only after journal COMMIT. The closed Pause journal format is unchanged.
Failed-tail diagnostics append to existing `cleanup.suffix_lines` within its
65536-byte UTF8 allowance, preserving earlier lines; excess new diagnostics are
explicitly truncated and logged. `execution_unknown` retains ownership independently
of diagnostic capacity. These lines are diagnostics, never quiescence proof or
permission to replay an unknown entry. A refused save retains failed ownership
and propagates unless an original exception is already unwinding; then that
original exception carries the settlement failure note and log. Every collected
outer-tail owner is visited even if another settlement fails.

Installed-layout unowned legacy producers refuse before claim/preparation; absent
layout preserves normal operation. No schema installation, public apply or COMPLETE
writer. D and supported launcher downgrade refusal remain NOT INTEGRATED; a table cannot make
a prefeature reader recognize a hold. Manage-agent cannot transfer teams.
C2 now reserves trusted thread/participant/INTEGER invocation/SID ownership before
preparation, then composes final claims and launch with the short gate, DB and
host terminal ordering. All purposes and actual host/direct429, eviction and
bounded-nudge paths participate. The existing thread audit owner stores strict
bounded append-only attempt/deferral evidence; no schema or task-Pause format
changes. Unknown prior retries stay blockers after later clean outcomes.
Captured actual running turns alone may settle under WAITING; no drain label
admits new work. Queue dedup spans queued/in-flight work, typed deferrals release
notification ownership, and complete durable runnable discovery/redelivery keeps
pending tokens/ranges/probe/catchup obligations intact. Native thread recovery
and raw reapers preserve held/C2-owned history. A pending receipt whose
producer settled without deferral or unknown cleanup and has no live owner
takes ordinary daemon_restart recovery; a shutdown cancellation after every
attempt settled quiescent records no failed_cleanup. Participant/session writes
cooperate during future forward reset; messages remain admissible. The bounded
thread census/drain and post-release seams are available to D, whose complete
startup/containment/operation/old-reader closure remains absent. No public apply
or COMPLETE writer; APPLY_AVAILABLE stays false. Behavior remains UNEXECUTED,
integration SKIPPED24342; no new product tests/fixtures or activation authority.
Native archive admission precedes delivery discard, BOOTSTRAP reaping, status,
participant resets, their audits and the archive system message in one DB-owned
transaction serialized against a forward decision. Refusal leaves all those
obligations and history unchanged; transcript files/events follow COMMIT.
Authentic provider eviction audit and compare-and-clear of the attempted stale
SID commit before fallback phase selection/admission. Only the actual live
producer's durable launched/settled initial attempt can record that observation
under a winning hold; callback/drain/new-launch and general participant guards
remain unchanged. Watermarks, delivery ranges/tokens and runtime SID are retained;
unknown cleanup remains a blocker. Deferred re-entry cannot reuse the evicted SID.
These archive/eviction/re-entry behavioral observations remain UNEXECUTED.

C3 now registers dream/wake/schedule/job native source, permanent org/agent and
owning session before preparation, rechecks actual claims and final host/direct
launch, and retains original retry budgets/SID/enqueue age on safe deferral.
Pending work stays native; recurring schedule occurrences retain distinct
history. Existing queue/timer release seams deduplicate redelivery; reapers
preserve held or uncertain owners. Complete C3 causal census covers job waits,
active bindings, remote attempts/workspaces, pending enrollment and writer tails;
terminal rows or missing trackers never prove cleanup. Native profile/workflow,
roster/name/settings/skill/policy/cleanup writers reserve affected IDs and
source/target membership, keeping their original synchronization and journals.
Strict bounded version1 producer/writer audits append in existing audit_log;
earlier uncertainty survives later clean outcomes. No extra table, permission
or auth change, remote engine, move/reset/publication service or COMPLETE writer.
D composes the canonical capture/coherent publication reader and complete early
startup/recovery/old-reader guard; D/E/F remain outstanding. APPLY_AVAILABLE=False.
All behavior/migration/recovery remains UNEXECUTED; integration SKIPPED24342,
Python units RETIRED, no new cases/fixtures or activation/live-operation authority.

C3 schedule preparation/spec/execution uses the SAME pinned runtime SID;
provider resume identity remains separate. Native caught materialization,
integrity/package/repository failures retain sticky failed_cleanup after source
terminalization; normal return is not cleanup proof. Agent custom-skill identity
admission precedes reservations. Authority/profile sync/async intervals expose
the same native disposition to explicit pre-effect validators, including stale
rename/prompt revisions and roster/profile fallback paths. Naming refresh must
complete its finite native transaction; caught reconciliation/publication/clone
failures retain uncertainty even on a normal return. An unfenced interval or an
HTTP status never certifies absence of effects. Exact validation exceptions may
settle only after native rollback and terminal reconciliation finish. Scratch
cleanup uses no-follow lstat: only ENOENT establishes literal absence; surviving
entries (including dangling symlinks), inspection errors and prior uncertainty
remain blockers, with actual cleanup causes retained in logs/exception notes.
Cancellation, partial files/packages, failed rollback or uncertain compensation
stay unknown. Existing unknown history is immutable; there is no clear writer.
Retry initialization explicitly freezes EMPTY as well as nonempty schedules;
missing legacy facts preserve history but cannot grant new retries or automatic
ambiguous reentry. These are source corrections only; behavior UNEXECUTED,
integration INCLUDING callback SKIPPED24342, APPLY_AVAILABLE=false and D/E/F remain.

Normal source acceptance does not authorize activation/live move. Preserve all
IDs/paths/history/frozen bindings/permissions and Engineering omitted-owner
routing. No new tests/fixtures, retired Python units, ad hoc E2E or protected
proof; section8 permits source/static acceptance with behavior UNEXECUTED and
integration SKIPPED24342. Exact-head independent review/scoped QA, lint-only
CI/hooks/nonforce publication and manager guarded merge remain. See the
[approved online team-move contract](docs/superpowers/specs/2026-10-14-online-team-move.md). Historical offline limits remain historical.

Founder THR296 seq107 / TASK11050 authorizes the separate online empty-team
correction: `happyranch teams ensure-default --org ORG` calls founder-bearer
POST `/api/v1/orgs/{slug}/teams/ensure-default`. It creates only an absent
`default` with `{kind: human, principal: founder}` and no workers. Compatible
replay preserves existing workers and performs no save, audit or publication;
conflicting management refuses409 before mutation. Registry serialization saves
the candidate before changing the shared object; the existing async writer gate
precedes teams_lock and the short canonical fence, with owned publication after
lease release. Only an absent display `default_team` pointer becomes Default;
explicit pointers and omitted-task Engineering routing remain unchanged.
HTTP200 requires current canonical/cache authority readback; HTTP202 means the
team exists but authority is not ready (CLI exit1). Retry does not repair that
partial state; existing supported publication/cold recovery retains ownership.
`happyranch teams list --org ORG` reads the roster. Add Agent already lists human
teams; refresh its teams read after provision, then select Default without
creating an agent. Source acceptance, loaded capability and actual live selection
are separate evidence. No startup seeding, live raw edits, agent move/creation,
offline migration, deployment/restart or broader administration follows. The
deferred offline move and its historical proof/correction limits below remain
unchanged; they do not gate this distinct empty-team operation. No new cases or
fixtures, retired Python units, integration or ad hoc E2E execution follows.

Historical TASK10394/TASK10553 PR1022 runs used the finite
L1/L4/L5/L7/L8/L9/W10 selections with the closed integration parent,
independently pinned b317 reader and transient exact browser tooling.
Merged PR1025/THR291seq69 supersedes that automatic PR entry with lint-only
checks and daily-only Product E2E; retained case source grants no new execution.
See `docs/local-ci.md`. Python units remain RETIRED and broad integration SKIPPED.
M, compatible restore, reboot, live operations and after-M acceptance remain
held. Source checks are not behavioral proof; preserve exits and missing evidence.


C10 uses the existing built SPA and isolated integration fixture, with en/zh-CN
at 390×844 and 1440×900. Authored availability/empty-team projections are UI
boundaries; real after-migration browser acceptance requires the actual C6 M
check/apply/receipt. Header locale selection transfers focus to its trigger;
modal external-tab locale updates preserve their focused drafts. Work Hours
roster loading/error/Retry is distinct from empty. Emitted completion guidance
references the canonical start-task skill: worker-owned roots have only
server-authorized `done`, self-only `delegate` or `escalate` decisions;
delegated leaves return plain completions. An attempted non-root founder
escalation fails the child and wakes its parent; manager-only operations stay
restricted.

The bounded roster maintenance source pins effective registration and the clean
Python3.14 reader, independently closes org/shared-store restore inventories,
and performs final pre-reset CAS. Complete/compensate and two same-manifest
replays retain native reset/publication ownership; compensation preserves the
original receipt and records its NEW coherent generation separately. C6/C8/C9
source and 28 publication/3 replay localizations are AUTHORED, not executed.
M launch/observer/reboot capability remains unproved; see the operator runbook.

Team managers may be existing agent names or closed tagged principals
`{kind: agent|human, principal: ...}`. The sole human principal is `founder`;
its executable manager is absent. Founder never enters AgentDef, all_agents,
workspace, callback or executor identity. Attachment validates duplicates and
both directions of active/pending roster and AgentDef role/team agreement.
An exactly registered pending bootstrap manager may attach only with matching
team/manager role and a unique active-or-pending identity. It stays absent from
the active launch roster and current policy eligibility; workflow authority
remains fenced until ordinary authenticated approval publishes an ACTIVE manager.
Missing/mismatched definitions and active/pending duplicates refuse attachment.
HTTP removal of a still-active declared worker is invalid and rolls back; native
registry add/remove roundtrips are separate serialization coverage.

Existing omitted task routing defaults to Engineering unless an explicit
`task_default_team` is present. Proven fresh org creation adds empty human
Default and writes both `default_team` and `task_default_team` as Default.
Selecting a human team requires an active executable owner before task,
attachment or queue persistence (`422 owner_required_for_human_team`). Malformed
active definitions after attachment also refuse owner admission before allocation. Workers
remain ordinary decision owners of roots, with self-only decomposition.

New workflow authority uses schema2 typed managers and both routing pointers;
retained schema1 bytes/digests and pinned workflow contexts keep their original
interpretation. Human manager principals are never executable role candidates
or policy-selector managers. Startup never moves or reconstructs the roster.
Human delegated outcomes name the valid persisted executable parent owner, or
`unknown_manager` on invalid ancestry. Only absent verdict maps implicitly;
blank/custom/standard strings remain exact. These are agent outcome records,
not authenticated Founder approval.

Selected human failed-leaf recovery uses exact positive INTEGER result identity,
complete selected-only history and separate guarded commits. The review/history
precede the consumed marker. A finite process-local operation retains the same
identity, phase and original parent effect through writer contention; 50ms
outside-lock retries have no count cap. Pending is never settlement, and
shutdown leaves authentic residue for next-start recovery. Native drain exception
or cancellation retains the original exceptional operation, phase and captured
jobs. Same-process same-result reentry returns recovery_required without
settlement, parent handoff or success-only bookkeeping; FAILED job rows do not
prove cleanup. Healthy finished same-result reentry renews the current
owner/history checks; cached done is not authority. Agent-team and
completed-leaf transaction behavior is preserved. See the current THR296 spec
and operator runbook; source authoring is not behavioral verification.

## Workflow authority publication (U2A)

`WorkflowAuthorityCoordinator` owns the org-scoped producer half of the
workflow authority contract. Every supported roster, team, repository,
executor/model, reviewer-set, or active-policy writer must durably fence the
org before its authority mutation and publish a coherent digest-addressed
generation afterward. A post-commit publication failure preserves the legacy
writer response but leaves the pointer fenced and machine-readable for cold
recovery; it must never restore a stale ready pointer.

The fence creates the publishing writer's invocation-owned `prepared` journal.
Another coordinator may supersede only that pre-file state; after file-phase
reservation, recovery owns completion. Snapshot reservation, canonical-file
replacement, and the pointer CAS all require the original journal and
`publisher_invocation`, so a stale coordinator cannot adopt a newer fence or
return old authority bytes to ready. Cold recovery re-captures against that
same pre-file journal without replacing it; an incoherent re-capture is
read-only and remains fenced, while concurrent supersession is refused by the
same binding checks. Selector initialization during whole-runtime state loading
(covering daemon startup and runtime register/switch before state publication),
before dynamic org attachment, founder creation
of a new-team manager, pending bootstrap-manager approval, and the legacy
policy GET/release compatibility handlers uses the same coordinated canonical
change. Repeated startup/dynamic checks and existing-team worker creation do
not advance the generation or selector history beyond their own authority
write. Every task/thread/dream/wake/schedule
launch resolver is read-only and refuses an uninitialized selector; it never
initializes authority while launching. An authenticated existing selector
remains a read and does not fence or advance the generation.

Multi-stage async writers take the process-local coordinator gate before
`teams_lock` and retain that gate through their terminal success or
compensation. Request cancellation or SSE disconnect drains started mutating
workers before releasing the gate; admitted request writes also finish their
terminal reconciliation before propagating cancellation, including repeated
cancellation. Startup migration retains the same process gate for its batch.
The shipped durable publication lease is acquired only for each synchronous
canonical mutation and released before filesystem scanning, awaited workspace
bootstrap, cloning, network access, host launch, callbacks, snapshot capture,
or other canonical-input scanning. SQLite stage transactions remain short.
Do not change the existing `org.db_lock -> binding_lease -> synchronized DB
callback` order. Direct same-UID file/DB mutation is outside this cooperative
guarantee.

U2A provides `verify_admission_ready()` for later workflow units but wires no
admission, activation, or dispatch consumer. U2B now ships the cooperative
same-host `ProfileCoordinator`: every supported machine-global profile/adapter
writer acquires the stable owner-only per-profile `flock` before any affected
org publication gate, pre-fences exactly the orgs with active or outstanding
consumers, commits through the existing durable-first writer, and republishes
only a complete coherent closure. The complete acyclic lock order is profile
lease -> org publication lease -> existing writer lock(s) -> the shared
`executor_profiles.yaml` store lock as an innermost leaf. Every save/remove
holds that stable mode-0600 `flock` only around its read/merge/`os.replace`
critical section; the store-lock holder never acquires another lease, writer
lock, or SQLite transaction. Publication paths never acquire a profile or
store lease, and no profile/publication lease or SQLite transaction spans
filesystem scanning, network, host launch, or callbacks. Active Founder creation,
Founder approval, manager revision-CAS executor update, dedicated executor
update, and explicit termination maintain distinct per-agent dependency rows
inside the canonical fence. Pending enrollment/rejection has no active profile
membership. Whole-definition repo/model writers also preserve this relation.
Lifecycle profile leases cover only synchronous canonical mutation and its
compensation, and release before publication capture or awaited bootstrap.
Missing dependency rows are never proof of an empty canonical requirement set.
A profile contender refuses an unfinished ordinary canonical batch before its
global operation claim; startup authority recovery owns interrupted batches. Removal leaves
an outstanding consumer unbound and the org fenced until an explicit coherent
rebind/removal. Cold startup completes interrupted operations once; a
post-commit republish failure preserves the writer's established response while
leaving a machine-readable fenced recovery state. Direct same-UID file/DB edits
remain outside the cooperative guarantee. Dynamic org attachment scans its
canonical profile requirements and authority inputs before taking the corresponding
profile leases. Discovery is bracketed by the existing durable authority revision
and revalidated under the profile-then-org mutation lease; changed captures
retry boundedly or refuse. Synchronization consumes that captured roster without
rescanning, publishes only a validated canonical snapshot and current profile
digest, and joins the shared org map
before releasing them; a non-terminal operation is therefore retried boundedly
or refused and cannot be escaped. Closure coherence compares every org mirror's
profile digest with the current global profile digest before readiness reopens.
Direct-connect route/sweep retries take the same stable profile lease, re-read
the durable projection terminal state before creating a U1A operation/fence,
and only the winner records that existing durable claim and mutates/publishes.
A genuinely empty default org remains attached with no agents and `teams=[]`
when its initial authority publication is fenced by the missing default reviewer.
Attachment proves absence of active and pending definitions and canonical/in-memory
teams outside leases and transactions, brackets that discovery with the durable
revision, and validates it under profile-then-org ownership. It preserves the
initial fenced generation and publication journal; `verify_admission_ready()`
still refuses `authority_pointer_not_ready`. Outstanding dependency or profile
operation evidence, unfinished canonical writers, and stale captures refuse this
exception before synchronization. Reviewer policy and snapshot validation do not
change; subsequent coherent canonical setup uses ordinary publication/recovery.

Adapter approval carries its initially selected intended profile (including no
target) through to the durable registry writer. Under the existing adapter
writer lock, that selection must still match before approval, an idempotent
return, or profile binding. A concurrent supported submission changing the
target refuses with the existing 409 `profile_consumer_changed` conflict before
adapter/profile mutation; ownership releases before a fresh request selects
again. A known target under live contention still returns 409
`profile_coordinator_busy`; ordinary no-target approval succeeds without a
profile. No new profile lease is acquired beneath the adapter writer lock.

U2B still wires no workflow
admission, activation, or dispatch consumer; those remain later units.

**Memory reporting core (THR-091).** Backend and CLI use one pure observation-only
reducer and consume only persisted version1 exposure metadata. Before empty or
short returns, backend reads a coherent bounded multi-table evidence snapshot;
CLI exhausts task pages and all relevant memory/lifecycle/control audit action
pages in two stable sweeps, comparing tasks, audit content and roles at a fixed
UTC cutoff. Task pagination uses `(created_at, id)` descending, with the last ID
as the server-resolved cursor; audit pages retain their own keyset ordering.
Accepted epochs additionally require complete original results/jobs/outputs and
stable serving bookends. Acquisition
failures refuse as `acquisition_unavailable`, with no partial CLI stdout. Tagged
G1 authority is revalidated through the shared reducer and serving audit view;
unversioned or damaged evidence remains `insufficient_instrumentation`.
`session_start` denotes intended invocations. Valid reports exclude synthetic,
recovery and pre-start tuples; raw sample counts alone cannot enable collection.
`by_session_kind` and `session_lifecycle` expose disjoint first-launch, job-resume,
retry and retry-resume diagnostics. Natural and excluded intervals both own
SID-less ends; excluded sessions never enter natural metrics. Any credited read or
search after retirement closes eligibility; valid earlier history remains.
Missing/ambiguous evidence yields unknown/null hard-zero fields, and absent kinds
report no observed population. Memory get/search credit trusts the supplied live
SID matching live org/agent context; deliberately supplying another same-agent
task's live SID credits that selected session. This is a known observational
trust limit, not authentication or hostile-caller proof; D establishes no actual
caller identity. Founder THR-091 seq401 declined `caller_task_id` and accepted A
on completed JOB-4624's other resume/retry/overlap/ended/absent/lifecycle/cleanup
results without rerun, making explicit foreign-live-SID rejection non-gating.
Original JOB-4624 exit1/raw FAIL and TASK-11402 QA REVISE remain historical and
unchanged. Ended/absent selectors remain uncredited and POST-retirement credit
remains hard-zero; earlier legitimate active reads remain valid. Restart, merge
and this documentation accept neither B, C nor an epoch; original admission,
current full health, immutable epoch and frozen observation rules remain.
An operational accepted epoch and its independent within48h health duty are
separate from source tests, review, CI and deployment. See the memory sections in
`features-and-invariants.md`, `web-and-cli.md` and the current corrective spec.

Retained pytest integration collection and execution use deterministic external CLI stubs through
`tests/helpers/integration_parent.py` before pytest/runtime imports. Use disposable
GitHub runners or a separately authorized Mac Linux guest; the live Linux daemon
host cannot run integration tests, including through jobs. See `docs/local-ci.md`
for parent isolation, explicit plans, source/callback identity and bounded two-org
exception observation. General integration remains SKIPPED under THR-243 seq42;
a focused task authorization does not establish full-suite health.

THR-291's separately authorized standalone `e2e/run.py` uses public bootstrap,
registered deterministic external stubs and actual CLI/HTTP/browser boundaries,
without pytest, integration-parent monkeypatches or product hooks. It runs only
on disposable GitHub Ubuntu with exact Python/browser inputs, Node24, bounded
owned-process cleanup and complete variant/cell receipts. Retained pytest
integration/platform/callback selections keep the existing parent fences.
`Product E2E` admits only the daily default-branch schedule under THR-291 seq69.
Scheduled runs38482572306 and38644465643 failed at their own sources; corrected
behavior remains pending the existing daily lane. Historical baseline is separate evidence.
The aggregate also requires real TERM/INT launcher diagnostics with non-success
and complete owned cleanup; product cancellation remains uncovered.
See `e2e/README.md` and `docs/local-ci.md`. Old units remain retired.
### Finite document-review templates and initial drafts (THR-139 seq410)

New `schema_version: 2` / `kind: document-review` definitions compile finite data:
one agent author, 1–3 independent reviewer slots, at most one existing Founder
human, immutable-document-revision output/description, all/current reviewer
membership, approved with optional changes_requested, and either null return or
return-to-author/new/all-prior-receipts. Submission timing is on-completion or
while-active-or-completed capability; it never requires an active author to submit.
Abstract role names are not principals or grants. Actual bindings still require
current same-org active canonical membership, independence and authority/profile
leases. No label, count or outcome supplies code or engineering authority.

The exact @2 compiler/validator/operator-input triple accompanies every immutable
draft/version. Activation request/receipt, authorization, binding and initial-draft
context use @2 families; the context retains both raw definition and compiled
contract. Author allocation and input visibility consume that pinned contract.
Legacy @1 raw bytes, pins, replay digests, task briefs and wire serialization stay
unchanged. Read by immutable definition/pins, never identity.version or current
pointer; full DATA/PIN validation checks all drafts and versions. A pre-generic
reader refuses an org with any @2 data. Such an org needs a compatible reader;
there is no old-reader downgrade promise or automatic history conversion.

F/E DDL, full layout/reference validation and explicit-only existing-org migration
remain unchanged; generic Database/runtime-audit remain workflow-free. Existing
queue, host, callback and recovery owners still produce truthful initial drafts.
Completion is draft-only: submission, review requests/receipts/joins, revision and
mounted UI remain later units. Source implementation is not review/QA/CI acceptance,
operator observation, deployment or Phase1 completion.

**H handoff storage — U3a(a), THR139 seq445/465.** Proven-fresh org creation
initializes H (complete G plus the exact `workflow_author_handoffs` table: 17
columns, six automatic constraint indexes, no marker or explicit index).
Existing F/E/G startup, reopen and enable validate their installed layout and
never add H. F/E must use the separately approved G migration first. The
explicit offline org-only command is
`python scripts/migrate_workflow_author_handoff_schema.py --org-root <absolute-org-root> --org-slug <slug> [--check]`.
For this H command, `--check` returns 1 for valid G requiring migration, 0 for
validated H; ordinary migration/repeat returns 0, refusal 1, parser error 2.
One short writer rechecks source ownership and complete independent references,
adds only the approved table, validates the target and retained rows, then
commits; failures roll back. Operator stop/ownership cooperation is required;
the script stops no process and cannot exclude an arbitrary concurrent start.
Even empty H requires an H-compatible reader. F/E/G SQL, IDs, history, permanent
NULL active legacy result and genuine INTEGER result links retain their meaning.
Whole-database F/E/G/H references preserve the independent Pause/Resume dimension;
generic Database/runtime-audit remain workflow-free and authority-v2 observed-only.

Complete H validation authenticates historical profile readiness from the saved
full dependency/store/registry mirror. Every closed variant preserves raw
nonempty source profile names; namespace and consumer identities are nonblank
UTF8 strings of 1..256 bytes without NUL, generations are exact integers in
0..9223372036854775807, and digests are full lowercase SHA256 strings. Native
row grouping/order and unrelated or removed rows remain intact. The historical
org's active requirements must have matching active store and registry
generations, and their full names, digests and ordered consumer membership must
reconstruct the pinned canonical profile projection. Empty mirror with required
profiles, missing/mismatched required rows or unbound requirements refuse
`workflow_author_handoff_storage_corrupt` before graph creation; an empty
required projection remains valid. Readiness, the offline script and producers
share this reader. Admitted GET exposes only category500; invisible scope404 and
selected-grant403 retain earlier precedence. Today's mutable mirror is never
historical evidence; a genuine later authority change remains a current hold.

New H structured witnesses compare canonical JSON bytes against authentic
retained or derived projections, including the row, immutable source intent,
activation/template and byte pins, context, snapshot, saved pointer and profile
closure. Boolean true/false cannot substitute for integer 1/0 in any such
witness. Exact integers and legitimate embedded snapshot booleans retain their
types; historical event numeric semantics remain unchanged. A mismatch refuses
`workflow_author_handoff_storage_corrupt` before graph effects, with the same
selected scope/grant precedence.

New F5 review-dispatch events also preserve types across every before/after
projection, immutable transition, adjacent event and final retained row. Each
intermediate projection anchors immutable fields to native rows and validates
mutable field types; the initial projection derives the producer's queued
values. Complete callback records and reused results retain native task_results
types, acceptance is exact integer 1, and host quiescence is genuine Boolean
true. Boolean/integer substitutions refuse before owned effects, using
`workflow_review_storage_corrupt` internally and the existing admitted H
category500 mapping. Authentic flags and historical S2 numeric semantics remain.

U3a(b) adds matched immutable `@3` template/activation/context readers,
`submissions.validate_handoff_graph` and the internal handoff/review producers.
Historical `@1`/`@2` families and pins retain their interpretation. An original
CURRENT author with stage+submit authority reserves immutable bytes only. SAME
operation fulfillment requires its genuine INTEGER/full callback and committed
successful/quiescent terminal proof, plus current authority and independence.
The atomic graph retains NULL legacy result, appends the authentic result link,
and creates one unsigned request per configured slot. Actual agent tasks alone
populate the ordered 0..3 reviewer-task IDs; roles, kinds, outcomes and timing
come from immutable template data. Inputs remain filtered to each recipient.

The review owner reuses the existing scheduler/native host lifecycle and
Pause/Resume final admission, publication, prompt reader and cancellation fences.
Completed-source discovery precedes workers and periodic enqueue hints; possible
launch without affirmative proof remains uncertain. No callback is a signature.
U3a(c) mounts the closed handoff POST/GET and unsigned selected-request GET,
with CLI/TypeScript/OpenAPI/native parity and existing F5 callback, replay,
cancellation and portability owner selection. This complete U3a composition remains
unaccepted pending renewed independent same-head assessment and current static gates.

Current Founder-owner handoff GET may read a superseded source, including a
configuration with zero human reviewers, only with current instance ownership,
the SAME active authorization pointer and collect-review grant, full immutable
closure and coherent current authority. Source/request-agent selection remains
strict; staging, consumption and review dispatch retain current-source fences.
After valid admission, corruption refuses500; failed/cancelled sources retain
their actual reconciliation flags. Otherwise projection precedence is disabled
(held/new_runs_disabled), superseded (held/source_not_current), changed coherent
authority (held/authority_not_current), then uncertain/missing proof and eligible
graph/callback states. Core stays identical; every held graph is hidden and GET
creates no domain, queue or launch effects. Callback-before-clear original-current
GET remains allowed; cleared sessions refuse through existing session admission.

Direct-submit remains deferred; reviewer staging/native denial are described above. Genuine
receipts/joins, revisions/reassignment/recovery, mounted operator UI and operator
acceptance remain later units. Full independent review, honest same-head QA,
unconditional exact-head Source Boundary Lint and Web Lint (Node 24), and
manager guarded merge remain required under THR291 seq69. Actual CLI callback
sources and isolation fences remain retained; execution is not a merge check.
Manager-owned bounded two-template public smoke must precede Founder invitation.
No live migration/enable/assignment/deploy/restart follows.

Founder THR-291 seq69 makes automated PR/main merge CI **lint/static only**.
Required checks at the final PR head and exact merge on main are exactly
`Source Boundary Lint` and `Web Lint (Node 24)`, both unconditional with no
path applicability. The manager supplies these two names to `pr_ci_waiter`;
`pr_ci_merge` retains its independent review/QA, pinned-head and guarded-merge
contract. Freshly observed GitHub protection has empty required contexts/checks
and `strict=false`; source configuration is not external enforcement.

Default/`all` and `web` run only source boundary lint (all), Node24 colour scan,
ESLint and TypeScript `--noEmit` analysis, with locked `npm ci` setup.
Behavioral tests and builds are outside all/pre-push/required merge checks.
Normal hooks stay enabled; never bypass hooks or the guarded worktree.
Independent full code review and scoped QA remain mandatory. Static checks
cannot establish lifecycle, callback, browser or platform correctness;
behavioral regressions can be detected only later when their coverage executes.

Fresh Product E2E runs **schedule only**, `0 19 * * *` (19:00 UTC / 03:00
Asia/Shanghai), on default-branch immutable `github.sha`. Baseline Python
3.12/3.14 and existing TERM3.12/INT3.14 launcher diagnostics feed
`Daily Product E2E Health`: exactly56 variants on each baseline and four execution
artifacts (`e2e-3.12`, `e2e-3.14`, `e2e-cancel-TERM`, `e2e-cancel-INT`).
Product Python support is unchanged. It is never a merge check. No local E2E alias,
manual/PR/push/release dispatch, auto-retry or extra schedule is supported.
Scheduled historical runs38482572306 and38644465643 were observed and failed
at their own immutable sources. Corrected behavior remains **PENDING** until
the existing daily default-source lane executes the repair; historical3.13
failures and all-six-cell evidence remain unchanged. Prior first-slice receipts remain
historical evidence at their own source, not daily/final-head verification.
Engineering_manager owns daily failure/missing-evidence triage through existing
tasks/threads; no new notifications or guaranteed response SLA is implied.

Python units stay RETIRED; `python` exits2. General integration is **SKIPPED**
under THR-243 seq42, never PASS. Web Vitest, native/platform, integration,
the named actual CLI callback and their isolation fences retain their sources.
Docs Manual build and the real tenant-isolation harness retain manual-only
workflows, outside PR/main. Manual/tag macOS packaging is a separate release
activity and invokes no fresh E2E. Legacy nightly integration remains separate
at 07:00 UTC, with its historical failures and fixtures unchanged; its manual
`all_only` input runs the same lint/static all selection. No legacy repair or
run is authorized by this cadence change.

See [the reset coverage gap record](docs/python-test-reset.md) and
[Local CI](docs/local-ci.md) for retained selectors and evidence limits.

Source boundary enforcement: `python scripts/check_source_boundaries.py` and
`python scripts/validate_source_boundaries.py` use only stdlib (local Python 3.12+).
`--source-root <root>` on the checker selects isolated repository-shaped sources.
Only infrastructure/orchestrator `*.py` are parsed; explicit daemon import/from
forms at module time are forbidden, including classes/ordinary flow. Syntactic
top-level TYPE_CHECKING/typing aliases exempt true branches but not else;
function/async-function/lambda nodes are skipped. No allowlist/opt-out, relative/
dynamic import resolution, alias dataflow, callable metadata inspection or
runtime isolation proof. Invalid/missing/empty roots and filesystem/decode/parse
failures return 2; violations return 1 with file:line/target and guidance; clean
scans return 0. No product imports occur. `boundary-lint` and `all` run baseline
plus bounded synthetic CLI validation; unconditional PR/push-main hosted
`Source Boundary Lint` uses Python 3.14 without project dependency installation.
See the project-layout boundary section and [Local CI](docs/local-ci.md).

## Memory census and report readers (THR-091 seq428)

A closed failed-before-exposure invocation stays in every attempted/persisted
count, ordinal, digest and seal, with no shown/natural-session denominator.
Only one ordered authenticated intent/identity/raised-terminal tuple with actual
failed lifecycle, correct parent/type/population, Boolean false expectation and
binding, INTEGER0 launch callbacks, explicit null success/returncode and complete
absence of exposure/callback/credited get/search qualifies for this class.
Full bounded audit/task/result evidence, independent counts/digests/seals and
stable semantic bookends are required; missing/unknown/duplicate/contradictory,
pending/moving/error or all-failure evidence refuses. Normal exposed/in-flight
checks, temporal post-retirement zero, source/boot/cohort, original epoch/reset
contract and thresholds remain. Classification grants no B/C/health acceptance.

Canonical CLI and HTTP task comparisons exclude only valid rendering timestamps
at pause.observed_at, pause.blockers[*].observed_at and child_wait.observed_at.
Raw evidence, unknown/malformed fields, lifecycle/pause/wait state, results/jobs,
roles and all serving generation/count/digest/seal/error facts remain intact.
Invocation-exception diagnostics add only correlated class/bounded frame-location
metadata; no exception messages/source lines/locals are added. Existing failure,
terminal, wake/thread and control effects remain. Original launch cause is unknown.
See the corrective memory spec and the maintained agent guides.

## Commands

PR1022's TASK10553 continuation retains all 224 C4 parameters and the finite
L/W source. Historical PR shards are superseded by merged PR1025's THR291seq69
lint-only automation; retained behavioral sources are not automatic merge gates. Feature-only phase tracebacks/checkpoints survive earlier
than final teardown; parent/command inner deadlines fail visibly within the
unchanged 90-minute hosted limit. W10 retains exact accessible unforced worker
selection using observed adjacent agent/team text. Held M fixtures no longer
import retired unit helpers, but M/restore/reboot/paired-observer/causal/repetition
proof remains absent. See docs/local-ci.md and the roster migration runbook;
no feature or maintenance readiness follows from source correction.

W10's owned HTTP proxy forwards actual daemon SSE headers and frames as streams.
Finite-request settlement excludes only streams whose headers were forwarded;
browser disconnect/fixture cleanup closes idle upstreams, and a 30-minute stream
deadline fails visibly. `C10-http-prefix.json` survives a failed browser oracle;
it is partial diagnostic evidence, never the completed 29-row browser receipt.

OpenAPI operator (no tests/lifespan): `uv run python scripts/generate_openapi_snapshot.py --check`
(default, non-writing), or explicit reviewed `uv run python scripts/generate_openapi_snapshot.py --write`
(atomic snapshot replacement).

```bash
uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m integration  # disposable venue only

scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force

scripts/build_web.sh
cd web && npm run dev
happyranch web [--no-open]

scripts/local_ci.sh              # default: lint/static only; corrected daily E2E pending after historical failures
scripts/local_ci.sh boundary-lint # source-only lint + synthetic validation
scripts/local_ci.sh python       # retired target: refuses with exit 2
scripts/local_ci.sh web          # Web static CI (colour scan + ESLint + typecheck)
scripts/local_ci.sh integration  # Python integration tests
scripts/local_ci.sh help         # List targets and caveats
# Full guide: docs/local-ci.md
```

`scripts/local_ci.sh all`, canonical/full suites, and pushes whose hooks run
such verification are durable-job-only whenever expected to exceed one minute
or the safe interactive-session window. The mandatory operational instruction is in the
runtime-materialized `runtime/skills/bundled/jobs/SKILL.md` contract: submit with
`review_required:false`, `persistent:true`, block on `waiting_on_job_ids`, and
verify the resumed job receipt (exact command, runtime path/version, exit 0).
Short/focused tests expected to finish within one minute may run in-session.

Integration tests spawn a real daemon and fake CLIs. Run them before changes touching daemon lifespan, `SessionTracker`, callback routes, queue recovery, or executor callback behavior.

## Engineering delivery gates

- **CI recovery:** For a pinned PR head and failure signature, allow one
  cancel/rerun at most. Then capture SHA, job URL, and log excerpt; classify
  branch-scoped repair vs mainline/environmental block vs diagnosis. Never
  re-dispatch an edit-forbidden CI-only brief unchanged after mainline drift.
  The `jobs` skill defines the full gate and the existing external-job terminal
  verdict still controls completion.
- **Bilingual Web delivery:** Include English and Simplified Chinese for every
  new or changed app-owned string in the same PR. Provide affected-state evidence
  in both locales at 390×844 and 1440×900, including draft, focus, selection and
  original-action preservation with no locale-triggered requests or mutations.
  Untranslated owned copy or unreadable/unreachable Chinese layout requires
  `REQUEST_CHANGES`. Automation checks structure and completeness; reviewers
  assess meaning and usability. Use the PR template and Web guide checklist.
- **Frontend handoff:** Before review/QA, supply acceptance/spec mapping;
  loading, empty, error, and populated-state coverage; auth/permission
  coverage when applicable; screenshot or deterministic-test evidence; and
  changes since prior review. Return incomplete handoffs rather than discovering
  missing proof piecemeal. Reuse the canonical behavioral doc-sweep and
  adversarial evidence checklists. A third fix-forward round requires a
  manager/reviewer structural-diagnosis checkpoint, while actual authority
  boundaries remain controlling.

## Code Conventions

- Type hints on all function signatures.
- `from __future__ import annotations` in every source file.
- Pydantic v2 for structured data.
- Use `StrEnum` for enumerations when an enum is appropriate.
- Follow existing patterns in `runtime/orchestrator/` before adding abstractions.
- Keep changes scoped; avoid unrelated refactors and generated metadata churn.

## Structural maintenance

- Put behavior in its existing domain owner; `database.py`/`Database` stays the
  shared interface, connection and coordination owner. Challenge unrelated
  additions to `run_step.py`, `models.py` and generic utilities. Use the
  [project layout](docs/agent-guides/project-layout.md) to locate responsibilities.
- Preserve standalone `python scripts/check_source_boundaries.py` and its
  local/hosted source-only lane: explicit module-time infrastructure/orchestrator
  imports of `runtime.daemon` are forbidden, with syntactic `TYPE_CHECKING` and
  callable-local exceptions and no allowlist. See the
  [boundary contract](docs/agent-guides/project-layout.md#source-dependency-boundary)
  for relative/dynamic import, alias and callable-metadata limitations. This lint
  does not prove architecture quality; respect the existing frontend
  [feature and layer boundaries](web/ARCHITECTURE.md#layers-strict).
- Every code review answers: is behavior placed with its owner, does the change
  introduce unnecessary coupling or duplicated ownership, and do the source map
  and behavior docs agree with the code? Correct concrete violations before merge.
- Repeated unrelated edits, merge conflicts, dependency cycles or scattered
  feature changes trigger bounded design consideration. Size is a signal, not a
  quota. Extract coherent responsibilities only for concrete maintenance benefit;
  retain compatibility-sensitive code where warranted, including intentionally
  retained bulk decline. E1/E2 remain deferred; do not reopen the decomposition
  program.
- Keep upkeep proportional, with necessary docs in the same PR. Record deferred
  debt with concrete impact and an owner; do not start automatic cleanup campaigns
  or impose tiny-move quotas.

The engineering manager enforces these rules at merge. Success means locating
and changing a responsibility without unrelated edits.

## Web Contract

Every browser-callable daemon route maps to one TS function in `web/src/lib/api/`.

All new or changed app-owned headings, actions, dialogs, tooltips, accessible
labels, validation, loading, empty/error states and generated narratives must
ship English (`en`) and Simplified Chinese (`zh-CN`) together in the same PR.
Use typed catalogs, named parameters, explicit plurals and locale-aware helpers.
Pure shared UI receives localized props from its callers. Authored content,
machine identifiers and raw diagnostics remain verbatim. Preserve drafts, focus,
selection, navigation and original actions across locale changes; a locale
change must not trigger a request, mutation or transport restart. See
`docs/agent-guides/web-and-cli.md` for the affected-state review checklist.

- Python snapshot maintainer: `scripts/generate_openapi_snapshot.py`; data: `tests/contract/openapi.json`.
- TS coverage: `web/src/test/openapi-coverage.test.ts`.
- Regenerate reviewed intentional OpenAPI changes with `uv run python scripts/generate_openapi_snapshot.py --write` (explicit atomic write; default/`--check` never writes).

## Native Impact Evidence

Before editing code, inspect the relevant definitions and affected callers, imports, and configuration consumers using targeted searches. Briefly summarize the expected files and symbols, affected behavior, material risks, and supporting findings in a progress message. Scale the investigation to the change; documentation-only edits need only a scope statement. Summarize findings rather than raw search output; no separate report file is required. If the scope expands, update the summary before making those edits.



Each attachment storage_key can be claimed by one task only, including within
the same tree. Each direct, child or then-leg list allows at most five refs;
keys must be unique across the entire initial decision, including siblings and
nested legs. Carrier top-level refs belong to the carrier; its first leg reads
through ancestry without a second claim. Initial fanout preserves both child
and nested attachment fields; later chain advancement keeps its existing rules.

Recognized initial pre-child errors include too_many_attachments,
duplicate_attachment, task_attachment_not_found, attachment_already_claimed,
invalid_attachment_display_name and unsupported_attachment_type. Feedback gives
the code, escaped key when supplied, and remediation. Correct in a genuinely
new authorized session/result; never replay the consumed result. Each
decision-owning task has at most TWO committed attachment-error feedback
opportunities shared across sessions and initial delegate/fanout/parallel.
Committed A consumes a chance even if B/enqueue is lost. The third recognized
error uses the existing root ESCALATED or nonroot FAILED disposition. A valid
correction after two remains eligible under current authority and revision
limits. Other authoring, revise, slice and total-step rules stay unchanged.
The exact-task append-only orchestration_step feedback audit adds
attachment_correction version1 with opportunity, accepted_result_id,
accepted_session_id, feedback_result_id and error_code. Strict history reads
validate genuine result/step associations; ambiguous history refuses without
repair. One synchronized BEGIN IMMEDIATE checks the ORIGINAL RetryClaim and
history/count, then commits A (feedback/PENDING) or the exhausted stop atomically.
B separately reserves the exact pending fingerprint through synchronous enqueue;
queue insertion is not a durable claim or crash-atomic exactly-once guarantee.
SQLite/OSError/transaction/postcommit failures retain their existing boundaries;
only six exact built-in ValueError shapes at initial validation/preparation
recover. Callback admission, receipt identity/history and once-only consumption
are preserved. Old schema readers do not enforce this new budget policy.
Stop tails use the captured winning owner and recheck before initiation. Existing
external cross-process/cross-await job, cleanup and notification limits remain;
source inspection supplies no behavioral or automatic-startup coverage.
Keep task-only evidence in output/<actual-task-id>, report output_dir and use
supported recall. Do not reattach an already-claimed maker key to a reviewer.
For a distinct attachment handoff upload a fresh authorized copy with
`happyranch attach-upload --org ORG --as-agent CURRENT_AGENT --file /absolute/source`
within the actual authorized environment (upload has no --session-id flag).
Inherited access grants reading, not another claim. Never upload task-only
logs/screenshots/review packets to Shared Artifacts as an ownership workaround.


**Verified retry transactions.** Final single/fanout spawn rereads the original
report claim and recorded retry evidence only after `BEGIN IMMEDIATE` under the
Database RLock. `Committed` alone enqueues children; `InvalidLineage` uses owned
atomic feedback, and `LostClaim` drops without writes. Ordinary revision delta
rolls back with spawn. Retry feedback admission holds a separate no-write writer
reservation through synchronous queue insertion, released on every path. It is
not a durable task claim or an exactly-once queue guarantee. Cross-root provenance
is bounded to 20 root records / 19 recorded supersession edges; failed history
and original parent links stay intact. See orchestrator-contracts.md.

**Delegated retry failures.** An exhausted per-slice retry lineage is durable
causal context for the owning manager, not a runtime `runtime_retry_ceiling`
escalation or automatic successor. The manager may dispatch revised work with
the mechanically required failed-child `revisit_of_task_id`, or propose
escalation through the existing THR-181 path; completed/superseded descendants
retire historical failed ancestors.

Run focused behavior tests for every changed domain. Before committing, run `git diff --check`, `git diff --stat`, and inspect the final diff. Compare it against the latest scope summary and explain any unexpected changes.

Stop and escalate before touching permission-model generation, auth or credentials, schema migrations or overloaded-column semantics, v0/v1 compatibility, or other high-risk or load-bearing work.


## Root task Pause and Resume (THR-292)

TASK-11437's bounded correction adds only coherent serializer child_wait
observations (256/query257 per parent,1000 inspected children/250ms per response,
no refill), exact current completed-report recognition and neutral unavailable
fallback. Delegated alone is no outstanding-child/running proof. EN/ZH ordinary
qualifiers reach list/detail/header/rail/timeline and both actual agent cards;
worse rollups suppress parent reasons. Fanout counts require exact selected IDs/
width and seven literal row categories; prior audit join never proves resumed/
completed parent. Unknown/job detail blockers read descendant cleanup unconfirmed
and retain raw IDs/reasons and fail-closed uncertainty. No Pause UI on lists or
agent cards. Only AgentDetailDrawer's local100vw cap is added; shared Drawer,
providers/routes/config, execution/consumption/wakes/stores remain unchanged.
Backend/protected proof remains unexecuted; finite synthetic frontend observations
are separate from lint gates and behavioral Pause acceptance. No live control,
deployment/restart or historical closure is authorized. See the current Pause spec.


Root Pause/Resume (THR-292) is an org-owned control, separate from TaskStatus/BlockKind. New Pause requires a NULL-parent in_progress root; Pending refuses409 without claiming it. Descendants inherit only actual ancestry. Final producer commitment arbitrates with the hold before ordinary/v2/workflow admission and binding. Committed work and genuine results drain; every retry retains the original SID/G/count/owner and budget and checks again. Unknown host/tree/ACTION evidence remains a blocker, including terminal diagnostics. Only actual ordinary-purpose running sessions (including normal v2-admitted manager sessions) captured by that hold generation retain the unreviewed job exception; founder work-starting origin actions refuse before mutation. Separate manager successor roots remain unheld. Org-only installation leaves generic Database/runtime-audit unchanged and requires a compatible reader for rollback. Read-only overview/CLI/TS contract is in docs/superpowers/specs/2026-10-10-root-task-pause-resume.md; PR2 owns web controls. Python unit source/gates are RETIRED under THR-291 seq40, never PASS; corrected product E2E behavior remains pending after historical failures. Required current CI and independent exact-head review/QA remain.

Pause reads all current non-recovery tracker bindings inside the serialized root/DB writer and keeps the short tracker mutex through COMMIT. Durable positively-running evidence, exact current task/agent/SID and server-admitted ordinary purpose must agree. New or moved bindings are considered there; ended or replaced SIDs are excluded. Advisory pre-request snapshots never decide capture. The tracker mutex is a leaf lock: no binding lease is acquired beneath root/DB, and no await, host/provider operation or backoff runs under these locks. Duplicate Pause preserves capture; Resume followed by a new Pause replaces previous-generation capture.

Unclaimed completion recovery retains the actual original return and SIDs while held, with no running deadline. Its 120-second deadline is pinned atomically with the accepted real admission/claim, binding and launch journal. Preclaim/admission waiting spends nothing; claimed recovery retains its expiry across Resume, duplicates, retry and restart, including committed no-launch failure. Unresolved original host/tree evidence is separately retained under the original session/owner/generation before recovery conversion. It remains an unknown drain blocker after callbacks, terminal lifecycle, restart or quiescent recovery, without owning a new producer. Known-quiescent originals add no unknown blocker, and deferred unclaimed recovery alone is not execution.

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
unavailable). Bounded Founder exception (THR293seq236): the Sidebar account row
and thread list/detail/message-author/participant-rail sites show the Founder's
ready current name alone with matching avatar initials, else the canonical
`founder` ID (unnamed, loading, unavailable, error or another org still loading);
agents and every other label keep Name · ID. Historical IDs, links, keys,
owner/filter/recipient values and
stored message text remain canonical/literal. Metadata follows existing focus/
reconnect behavior and bounded 30-second polling; availability is never a global
gate for established ID operations. Editor drafts are scoped by org/kind/ID and
survive locale changes and identity refetches.

Real thread compose/follow-up/invite/forward and task read-filter pickers search
current names and permanent IDs. Selected values stay IDs across renames. Thread-detail
composer @mention hints offer only eligible participant agents, including their
current Name · ID labels and permanent-ID matching; human Founder is never
suggested, even after renaming. Manually typed current Founder names and
`@founder` remain literal and retain existing preflight/send behavior. Human
recipient controls elsewhere still offer the separate Founder option. Clear
invalidates pending task-filter resolution; stale success/error/finalization cannot
reapply a cleared filter, show an obsolete alert or release a newer attempt. Founder
is a separate human option routed as established `@founder`, never an agent
participant or task owner. There is no task-create owner GUI in the present web
source; the existing task agent filter is the relevant selector. Prospective
invite resolve omits `thread_id` because that optional scope means existing
participants; the actual invite endpoint retains final action eligibility.
Recognized former body/recipient tokens stop UI-managed associated attachment
uploads when caught by separate preflight. Body intentions follow the THR310 caller policy below,
with ASCII quotes eligible, explicit blockquote lines literal and email/code/escaped tokens excluded. This read
is not a
reservation: final server admission may reject after an intervening rename, and
already-completed uploads are not claimed rolled back. New en/zh-CN text uses
existing controls/tokens. The naming browser adapter captures 390×844/1440×900
light/dark states in en/zh-CN. Source-pinned captures and interaction receipts are
maker evidence; they do not establish pixel fidelity or independent acceptance.

The four existing naming adapters mirror IdentityView/ResolveResponse/RenameBody.
The repository contract snapshot is a summarized route/parameter/status artifact,
not full JSON Schema. Its four entries were originally authored from source;
the supported standalone generator and genuine served-schema comparison verify
that summary independently. Source-pinned schema/frontend/browser receipts live
in the maker handoff; final-candidate independent review and behavioral QA remain
separate requirements. The shipping-page MSW assertions in
`web/src/test/identity-names.test.tsx` add UI portions of scenarios2/3/4/5/8/10;
API assertions are `web/src/lib/api/identities.test.ts`; scenario9 also exercises
canonical skill-row attributes and stable row identity during label refresh.
These provider/component tests use frontend mocks. Source-specific commands,
red/keeper results and actual execution status belong in the maker handoff;
frontend results do not establish daemon/DB end-to-end behavior. Existing
twelve-scenario integration bodies/fixtures are preserved. Python units remain RETIRED under THR-291 seq40;
general integration remains SKIPPED THR243seq42. Published source and maker verification do not establish independent acceptance,
merge, deployment or whole-feature completion.


The original PR1022 L4 run at `6472d886` retained an internal timeout exit 124,
not complete recovery proof. Its incremental traces identify a missing bound
completion audit for successful human-team leaf recovery, two failed-recovery
reads of a nonexistent supervisor method, and fixture startup fingerprint
refusals. Human-team completed recovery now atomically records the actual
INTEGER result and recovery session in its completion audit; historical
agent-team payloads retain their prior shape. Failed recovery reads shutdown
from the existing admission controller without changing host enforcement.
Test-side compiled-code fingerprints use marshal format 2, retaining full
code comparison while excluding object-sharing reference flags. All 224 C4
parameters and their original behavioral assertions remain. These corrections
are source readiness; final-head hosted execution and the existing held proof
inventory remain required, and no partial result is a feature-readiness claim.

C10 canonical enrollment readback includes the existing AgentDef writer's final LF.
The exact submitted draft/POST, focus/selection and all 89 assertions remain;
four readable draft screenshots do not establish completed 29-row browser proof.


The original PR1022 L4 archive at `18e01cfa` retains parent/command exit124,
45 failed calls, 24 passed calls, two setup errors and one interrupted setup-only
case; it is not complete 224-case proof. Its task teardown emitted the existing
informational `task_scratch_report`, which the human failed-recovery reader had
incorrectly treated as foreign causal history. That exact report action remains
informational. Failed recovery permits only the native task-scoped memory
observations: `memory_added`, `memory_digest_impression`, `memory_search`,
`memory_runtime_intent`, `memory_runtime_identity`, `memory_runtime_expectation`,
`memory_runtime_binding`, `memory_runtime_launched`, `memory_runtime_terminal`,
and `memory_collection_seal`. Agent-scoped memory read/update/promotion/lifecycle
rows are outside this task history; collection acceptance/invalidation controls
are not informational recovery exemptions. The exact native launch/refusal
observation `task_native_launch_boundary` is also informational; it does not
select or consume the recovery result. Unknown actions, including a
dictionary-payload `memory_unrecognized_transition`, refuse as
`ambiguous_history` before failure, review, marker, job or parent effects.
Selected result/session/owner, review ordering and replay fences are unchanged.
Fixture jobs are submitted
by the original ordinary child before clean omission, never by the purpose-bound
completion-recovery session. The restart-only no-job observer excludes the initial
marker-interruption launch. Native diagnostic events link fully flushed JSON
without replacing existing events, and actual job-submit refusals are retained
before assertions. All 224 C4 cases and their behavioral assertions remain.
These are source corrections only: no new behavioral execution, GREEN,
causal/restoration/repetition or held migration/restore/reboot proof is claimed.


- **Owner review discovery (THR139 seq506).** Current owner GET `/workflows/activations/{activation_id}/review-selection?proof_kind=owner-human` returns closed `workflow-owner-review-selection@1` navigation IDs, read-only r1 collection and complete advisory return readiness. Relational owner/grants precede decoding and renew at the coherent read boundary; no domain/lease/event/queue/join writes. I and separately admitted selected readers remain required. POST independently retains atomic replay/CAS/current checks. Old daemon discovery is unavailable with dependent actions disabled. Native policy unchanged; mounted UI and behavioral acceptance remain separate. See the four maintained guides and product-design-u0 specification.

Naming namespace capture includes active/pending executable agent managers and
workers. Mainline typed human Founder managers have no agent definition and are
excluded from that agent set; Founder remains the separate human naming owner.
The combined I/roster/naming behavior remains unverified; see the current naming spec.

**Empty-only coding schema K2a1 — Founder THR139seq562/329, ROOT TASK10693.**

Founder THR139seq562 approving559/329 and sole ROOT TASK10693 authorize this
bounded source slice. Layout J is exact I plus `workflow_coding_mappings`,
`workflow_coding_runs`, `workflow_coding_attempts`, `workflow_coding_events`:
3987 UTF8 SQL bytes/SHA256
`2c4b556671ef76582cdcbaed1e68d8575d9648ce29773f52c3d947ceaeeb0d70`,32 event
kinds,17 expected automatic constraint indexes (4/2/7/4), no explicit index or
marker. F–I DDL/history and document @1–@4/r1/r2/one-human formats remain literal.
The complete owned-layout reader and every metadata/cutover path admit J ONLY
with all four coding tables empty and valid retained I/document/native data.
Partial/extra/changed layout, any populated coding table, reserved coding
format/pin/import/source ownership or native coding audit without its readers
refuses; it never returns legacy. Arbitrary document content is not a family
discriminator; formats/kinds are checked in owned metadata. K2a2 must supply complete populated saved-data
readers before any coding producer, including setup/import/mappings.

Independent whole reference selection retains all six layouts F–J, all four
fresh/v0/v2/v2-organic histories, naming0/1, Pause and team-move combinations,
the five-component cache key and whole historical preimages. No candidate SQL
baseline or object filter is permitted. Failed reference construction remains
unavailable. Authority-v2 remains observed-only; generic Database/runtime-audit
remain workflow-free. The proven-empty J initializer primitive serves reference
construction, validating the two completed native remote-schema marker rows and
requiring every operational table empty; no schema object is excluded from
comparison. Actual create-org still explicitly initializes I. Startup/reopen/
enable validate, never install J; I remains usable for document work and gives
actionable offline guidance. Empty J uses the existing I draft/submission/handoff/
revision and document drain readers. Any J, even empty, blocks pre-J downgrade;
retain compatible J code or a separately approved verified pre-J backup. No
stripping, automatic migration or old-reader downgrade claim follows.

The operator source `scripts/migrate_workflow_coding_schema.py --runtime-root
/absolute/runtime --org ORG [--check] [--confirm-cooperative-offline]` implements
the approved cooperative offline I→J path. All invocations open read-only first:
valid I --check returns3 (migration needed), compatible empty J replay returns0,
and invalid or unavailable observations return1. Only exact I with explicit
cooperative offline confirmation can reopen mode=rw. The independently resolved
owned org path and pre-open device/inode/mode observation bind the RO PRAGMA
main path; ORIGINAL identity survives RO close/RW reopen, is compared before
and after writer open, after BEGIN EXCLUSIVE and before commit. Main/path and
attachment checks, regular/single-link/current uid, org root/slug/marker and
symlink constraints retain the shipped G pattern. Missing observations or known
path/file/lifecycle drift refuse with original-owner remediation; the writer's
replacement file never becomes a new source baseline.

This is cooperative pathname/file observation, not independent native descriptor
proof or hostile/direct same-user swap protection. Founder270 approving265/269
and Founder562 preserve that limit. Step43 withdraws step41/42's excess native
opened-file bridge requirement; their categorical binding_unavailable refusal,
FAILED11447 and adverse11446/11448 receipts remain historical. No native bridge,
extension/dependency/VFS/raw-pointer/ctypes change is supplied or needed under
this model. Direct same-user file/DB swapping between observations remains a
disclosed cooperative limit; source-only delivery cannot certify its behavior.

Writes additionally require documented daemon stop, reconciled hosts/outer tails
and exclusion of cooperative restarts/publication/profile/move until return.
Neither confirmation nor PID absence, TTL, terminal labels or producer_settled
uncertainty releases ownership. Complete source-idle checks retain publication/
profile, F5/draft/recovery/source claims, Pause release and team-move reservations.
Exact I/source SQL types/rowids/bytes/FKs, unchanged retained equality, four empty
additions, complete independent J validation, FK ON and rollback remain required.
The primitive receives the script's original source identity and independently
compares it, never derives a replacement baseline. Runnable source is supplied;
valid check/replay/migration/rollback and drift refusal remain UNEXECUTED, with
no migration-safety or OS isolation claim. Earlier scripts keep original targets.

The finite shared discriminator inventory covers typed scalar fields and
structured metadata using the same rules. Malformed lexical member scanning
recognizes complete, escaped, duplicate and incomplete reserved envelopes
without searching quoted document prose. Authority `source_pin` and coding
setup namespaces are checked independently of canonical/hash-valid bytes,
including orphan rows and current authority pointers. Typed scalar/native
`business_purpose=coding-replacement-selection@1` participates in the same
valid/malformed/duplicate/escaped/incomplete metadata rules. Ordinary
worker_execution/session purpose and free document prose remain noncoding.
Recovery.classify_task runs the empty/reserved boundary under its existing lock/error fence before
no-workflow and no-root/draft/F5 legacy returns; any coding-family schema
object, including unknown/case-changed names or wrong types, also requires the
complete central reader. Names and owning-table names are each checked for
tables/views/indexes/triggers; no old ownership alias is needed. Missing org identity
reconciles categorically before either legacy return. Unknown/malformed coding
returns reconciliation_required, with no coding ownership, task writer or dispatch.
Ordinary noncoding legacy and the existing document draft/F5 paths remain.

K0 stays disabled; K1 current resolution still refuses before private decode or
object I/O. No capability/setup/import/mapping/adoption/start/stage/native task/
event/audit producer, new route/DTO/native permission/dependency/hook, live SQL,
rollout/deploy/restart/assignment or trial renewal is delivered. Source inspection
and lint/static receipts cannot prove migration, readers, security, behavior or
upgrade safety: all remain UNEXECUTED. Python units RETIRED; general integration
INCLUDING callback SKIPPED24342; broad/negative/mutation/repetition/protected proof
UNEXECUTED/FOUNDER-WAIVED139429/430. Full independent review, same-final-head scoped
QA and Source Boundary Lint + Web Lint(Node24)/local all remain required, followed
by manager guarded merge. Root owns K2a2–K6 and operational/real-agent acceptance.


## Saved coding grammar source — K2a2a

Sole ROOT TASK10693, manager step48 and Founder THR139seq562/559/329
bound `runtime/workflows/coding_saved_records.py` to pure, unmounted saved-record
inspection. All eight entry points check complete canonical bytes, strict types,
bounds and contained equalities. Original command bytes/IDs/CAS/selectors remain
intact; contextual coding repository roles preserve K1 identity/owner invariants
without widening document/K1 readers. Parsing creates no adoption or authority.

Accepted TASK11459 B2/D1 appendix SHA256
`5a1baa4967ffefb07967d1aa5e5771b6048801bfcd71b37d690ab74aa3722c9b`
closes ready/pointer/profiles/full mirror, exact Founder provenance and complete
five-column native admission plus capture on issue AND withdrawal. Native v1/v2
snapshots retain their complete original projection and ordering; reconstruction
includes all consumers, generations and digests. Genuine empty profiles differ
from missing evidence; other-namespace/removed/unbound/unused mirror rows remain
validated. Historical pointer/journal/fence are retained, never read from today.

Grant selection is position-specific: setup/issuance terminates in full original
issue/withdraw Q; mapping uses closed M with 1–8 ordered distinct complete U
setups; capability selects its permitted U/P parent. Complete preimages/hash,
ID/parent/time/source ownership, source versus mint admission, level/action,
queued-null versus actual bound SID and component/argv radius attenuation are
checked without format fallback or truncation. Each ancestry ends at U→issue Q,
with at most64 capability nodes/16MiB decoded ancestry and original1MiB limits.
Mapping cannot union one repository's setup onto another. U subtree digest is
not the containing native issuance-wrapper digest. Fixed repository/base/head
and provider subject are separate from navigation. Saved ready/Founder strings,
a hash or a parsed grant never prove authentic native rows or current access.

Full relational/native↔J/event/history readers remain required BEFORE every
producer: identical genuine admissions and historical cache_installed journals,
setup issuance-subtree/native-wrapper/pointer/withdrawal, source result/start
claim and native task/session/parent chronology, mapped role eligibility and
independence, mapping generation/adoption authenticity, run/context/binding/
activation/request/receipt/contributor closure, journal replay/CAS and host/
cleanup/provider/effect ownership. Current-use owners revalidate every setup/
ancestor/grant, cancellation/Pause/move and actual executor permissions after
waits. Valid revoked history permits no new launch/disclosure/effect. No store,
roster, object, provider, DB or network I/O, resolver or trusted flag is supplied.

K2a1 still refuses ALL populated/reserved coding data including validate_data=False;
F–I/J schema/reference/cache/recovery/migration owners, create-org I and document
history are unchanged. K0 stays disabled and K1 current resolution unavailable.
No mount, native admission insertion, producer, transport/auth/permission change,
live setup/migration/assignment/deployment/restart or retired trial follows.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; behavior/
security/negative/mutation/repetition/protected/executable proof UNEXECUTED/
FOUNDER-WAIVED139429/430. AST/raw-byte inspection and lint/static checks establish
no behavioral or test-authoring PASS. Fresh FULL independent review then
same-final-head scoped SOURCE QA and manager guarded merge remain required.


## Retained native setup issuance history — K2a2b1

Sole ROOT TASK10693 step50 / Founder THR139seq562/559/329 adds unmounted
`coding_authority_history.read_setup_issuance_history`. The caller must admit
the complete native schema and own a coherent read transaction/connection/lock
with raw native storage values. Fixed-table parameterized SELECTs only; no
BEGIN, commit, writer, repair, resolver or injected truth flag is supplied.
The org-wide setup namespace inventory checks complete canonical issuance
wrappers, exact native types, source pin `coding-capability-issuer@1`, namespace,
ID/revision/time, full original command, expected-revision CAS and unique
Founder operation keys/setup subtrees. The selected native authorization ID
resolves one contiguous same-namespace prefix with a genuine prior issue for
withdrawal. Setup IDs and authorization row IDs are distinct identities.

For every issue AND withdrawal in that prefix, the complete five-column native
admission matches the saved command digest/Founder principal/org publication
namespace/generation/ID. The named complete twelve-column historical journal
must match namespace/generation/expected generation, exact full snapshot
bytes/digest/profile fence and cache_installed state, without native NULL/type
coercion. The shipped pure readers reconstruct saved v1/v2 snapshot/profile/full
mirror evidence, including genuine empty profiles. Neither today's pointer,
cache, roster nor mirror is historical proof. Exact setup_bytes are the issue's
canonical subtree; its digest differs from the native issuance-wrapper digest.
Valid old issuance remains history after withdrawal and never restores activity.

All three SELECTs gate every projected value by exact native storage class
before Python fetch: TEXT plus byte length via CAST AS BLOB (including multibyte
characters and embedded-NUL suffixes), BLOB plus byte length, INTEGER scalar
only. Invalid/oversize values return a bounded NULL refusal sentinel while
original typeof evidence stays intact. Genuine nullable file_phase_owner NULL
is preserved; accepted values retain exact historical bytes/types without
coercion or truncation. Each projected text/BLOB value is at most1MiB.
Inventory admits at most1024 org setup rows; one additional bounded row can be
fetched only to refuse overflow. After each bounded fetch, the shared budget
enforces16MiB cumulative accepted raw native text/BLOB bytes across inventory
and required admission/journal reads. This is retained-byte accounting, not a
total process-memory ceiling: SQLite engine work, rejected fetched rows and
decoded/copied structures are outside that accounting. Overflow/missing/
mismatched/ambiguous/malformed/unsupported evidence refuses categorically.
Only the selected prefix's admissions/journals are established, not unrelated/
later histories. No live human, current grant, source task/SID/result or populated-J
admission follows. Full capability/task/result/session ancestry, mapping/J/run/
event/receipt/heartbeat and current-use closure remain NEXT serial units before
any producer; K2a2 is incomplete. No mount/OrgState/DDL/reference/cache/recovery/
migration/selector or public/CLI/TS/auth/permission/dependency/hook change.
K2a1 populated/reserved refusal INCLUDING validate_data=False, create-org I,
document history, disabled K0 and unavailable K1 current resolution stay intact.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; behavior/
security/migration/negative/mutation/repetition/protected proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Source/static evidence is not behavioral acceptance.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
sole ROOT10693; no operational action or trial renewal is authorized.


## Retained mapping history and setup links — K2a2b2

Sole ROOT TASK10693 step53 / Founder THR139seq562/559/329 adds unmounted
`coding_mapping_history.read_mapping_history`. Caller admission of the complete
native/J structural schema and one coherent raw-value read snapshot/connection/
lock precede this SELECT-only call. It neither starts nor ends a transaction.
The selected native mapping ID resolves its complete same-org/release generation
prefix, never the current default. All ten native columns and original typeof
labels are retained, with full saved mapping bytes/digest, original complete
command/digest/key, strict INTEGER generation and expected_generation CAS,
original time and operation principal independently compared. Attribution follows
`WorkflowTemplatePrincipal.operation_principal`: agent:<issuer.principal>,
separate from raw role IDs and human:founder issuance provenance. Missing/gapped/
forked/conflicting operations refuse; no newest-row or same-digest fallback.

For each required mapping, its actual complete five-column admission matches the
original command digest/agent operation principal/capture namespace/generation/
ID. Its historical twelve-column publication journal matches full snapshot bytes/
digest/profile fence/expected generation/cache_installed and original nullable
owner. Shipped `read_saved_mapping_record` checks complete canonical mapping/
command/adoption/M grammar, setup prior-source and adoption times, repository
radius and all binding/adoption/capture pin equalities. Each M.setup resolves by
setup ID AND full canonical bytes to one genuine issuance.setup subtree, then
its complete earlier issuance/admission/publication prefix. The private native
setup composition shares the existing history owner; wrapper hash is distinct
from setup hash. Founder issue/withdraw semantics and authentic later withdrawal
remain historical facts; this read never restores a grant. No current pointer,
roster, cache, filesystem/store scan, adoption or remote-origin attestation.

Every projected value in all four fixed-table parameterized SELECTs gates exact
SQLite storage class BEFORE fetch: TEXT byte length via CAST AS BLOB, BLOB byte
length, native INTEGER scalar; genuine owner NULL remains NULL. Invalid/oversize
values become bounded NULL refusal sentinels with original typeof evidence, not
coercion/truncation. Per text/BLOB value at most1MiB; each mapping/setup inventory
at most1024 rows plus one bounded overflow fetch solely for refusal. Across the
whole composed call one shared budget limits8192 accepted native rows,16384
traversal steps and16MiB cumulative accepted raw text/BLOB bytes after bounded
fetch. Call-local indexed inventory/verified history can avoid repeated reads;
no per-root/recursion budget reset. SQL engine work, rejected-row and decoded/
copied structures are outside this accounting; no total memory ceiling claim.
Existing `read_setup_issuance_history` signature/result and retained semantics
remain unchanged; its established smaller inventories fit these added call caps.

Return values establish complete inventory grammar/attribution/operation
uniqueness and selected mapping-prefix admissions/publications plus the required
setup issuance prefixes only. They do not authenticate actual issuer task/SID/
policy/source_rule/current grant/capability/J graph or live store observations.
Full capability/native task/result/session/ancestry/J/run/event/request/receipt/
contributor/heartbeat/current-use/recovery/cutover readers still precede EVERY
producer; K2a2 remains incomplete. No mount/__init__/OrgState/schema/reference/
cache/recovery/migration/selector/producer/public/CLI/TS/native/auth/permission/
dependency/hook change. Populated/reserved coding refusal INCLUDING
validate_data=False, create-org I, document history, disabled K0 and unavailable
K1 current resolution remain intact. Root retains K2a2–K6 and genuine-agent/
two-org/override/backup-copy operational delivery. Accepted C01/C03/C09/C14/C15/
C16 and appendix variants remain source-scoped; no new case study/execution.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; parser/SQL/
product/security/negative/mutation/repetition/protected proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Static lint/source assessment is not behavioral proof.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
sole ROOT10693; no maker verdict/merge/operational action or trial renewal.


## Retained native capability ancestry — K2a2b3

Sole ROOT TASK10693 step56 / Founder THR139seq562/559/329 accepts capability
encoding contract SHA25638d80c7a4cc7a3283fdea37dd6f21fb366df9c2eba5fc47415ebe2061051fc7f
following independent DESIGN PASS11478. Unmounted
`coding_capability_history.read_capability_history` adds the retained capability
closure after the earlier K2a2b1/b2 checkpoints. Their historical descriptions
of then-missing capability readers remain history; full K2a2 is still incomplete.

A selected actual authorization ID resolves all seven native columns: ID=P.id,
namespace=org/{org}/coding-capability/{P.id}, INTEGER revision1, complete canonical
P BLOB, full-P SHA256 TEXT, literal workflow-coding-capability@1 source pin and
exact P.created_at TEXT. ID/namespace/digest aliases conflict rather than act as
fallbacks. Each P's OWN complete five-column native admission matches its capture
ID/org publication namespace/INTEGER generation, H(complete P) request_digest
and historical agent:<P.issuer.principal> operation attribution. Neither parent
capture nor original source_admission_id substitutes for a child's admission.
H(P) is finite; capture.grant_digest still hashes the complete upstream P or U.
Historical attribution does not authenticate a currently acting source/service.

The existing pure saved reader validates the selected complete canonical P/U/Q
ancestry once, including immutable source ownership/admission, level/action,
component/argv radius attenuation, refinements, queued-null versus bound SID
and effect subject equalities. Every embedded P must then resolve to its OWN
native row with byte-identical complete P, exact envelope, own admission and
historical publication capture. Terminal U resolves by setup ID AND complete
canonical subtree bytes to the actual containing issue plus complete preceding
issuance/admission/publication history. U ID is not an authorization row alias;
H(U) is not H(issuance wrapper). Terminal original ISSUE Q stays intact. Missing,
duplicate/conflicting/cyclic/cross-org/wrong-type/mismatched evidence refuses
with one category, no private bytes or SQL diagnostics and no partial history.

Caller must admit the complete native/J structural schema and own one coherent
raw-value read transaction/connection/lock. Fixed parameterized SELECTs only,
no transaction control. All new/reused projected values gate exact SQLite storage
class and byte length BEFORE fetch; TEXT uses BLOB byte length including NUL
suffixes/multibyte characters. Original typeof/refusal sentinels and genuine
nullable journal-owner NULL survive; no coercion or truncation. Each variable
value is at most1MiB. One shared whole-call budget permits8192 accepted native
rows,16384 traversal steps,16MiB accepted raw TEXT/BLOB bytes, setup inventory1024
and selected capability ancestry64. One bounded overflow fetch only refuses.
Call-local setup caching retains complete histories; no recursive budget reset.
Enclosing/decoded saved bounds remain and can refuse before64 nodes. These raw
byte limits do not bound total SQLite/process/decoded-copy memory.

`CodingCapabilityHistory` retains source-to-selected native capability facts,
all seven columns/original typeof/matched own admissions, and actual setup issue
history. Later withdrawal preserves history without restoring any grant. No
current pointer/cache/roster/store/object/network is read. Necessary timestamp
equalities/order do not prove actual mint order, source execution, native
allocation/task/SID/result/assignment/ancestry/policy, acting-service ownership
or full J graph. Complete native source/action-claim/task/session/result/policy,
run/context/binding/activation/event/replay/request/receipt/contributor/heartbeat,
current-use/recovery/cutover readers still precede EVERY producer. No source-rule
verifier, current permission, genuine launch or provider effect follows.

Existing setup/mapping APIs and source owners are unchanged; their bounded
private composition suffices without a new helper or owner edit. No mount/
__init__/OrgState/producer/schema/reference/cache/recovery/migration/public/CLI/
TS/native/auth/permission/dependency/hook change. Populated/reserved refusal
INCLUDING validate_data=False, create-org I, document history, disabled K0 and
unavailable K1 current resolution remain intact. Original contract9f0cab1c,
appendix5a1baa49/cases17d50aa2 stay byte-preserved; only the four accepted native
envelope/own-mint/full-ancestry/history-and-bounds variants are appended to task
case evidence. Python units RETIRED; integration INCLUDING callback SKIPPED24342;
all SQL/parser/product/security/negative/mutation/repetition/protected executable
proof UNEXECUTED/FOUNDER-WAIVED139429/430. Static/source assessment establishes
no behavioral or test-authoring PASS. Fresh FULL independent reviewer then
same-final-head scoped SOURCE QA return to SAME root, with actual lint/static
checks, normal publication/hooks and manager guarded merge. Root retains full
K2a2–K6 and real-agent/two-org/override/backup-copy operational acceptance; human
trial remains retired and no live operation is authorized.

## Retained native source identities — K2a2b4

Sole ROOT10693 step60 / Founder THR139seq562/559/329 accepts TASK11484
source appendix SHA2564ee1d1819d3b58ff1350b192d6438fa8ed8bf99ba7cc06abfb0fe315fb4312e1
and cases SHA256f55f71a1db09cf708dbad083cd8c955cd5241cf5f244586eaa3b19d7909c24ae
after independent DESIGN11485. Unmounted coding_source_history now adds
read_source_native_records(conn, expected_org_slug, authorization_id), with
exactly five immutable fields: capability_history, source_task_record,
ancestor_task_records, session_start_audit_record, same_session_result_records.
Existing capability/setup APIs and mandatory-value validations stay unchanged.

Select genuine source-level P and its complete native P/U/own-admission/historical
publication closure with one shared call budget/cache. All27 task/18 result/6
audit fields preserve exact native storage classes, genuine NULLs, empty free
text, original UTF8/JSON bytes/timestamps and finite native REAL cost. Current
source and complete parent-to-NULL ancestry (20 rows INCLUDING source) are
TODAY'S coherent records, never original source admission or current permission.
Ancestry follows only parent_task_id; missing/cyclic/foreign-team rows refuse.
Original source SID selects one authentic manager_decision session_start with
the exact native owner payload grammar and the complete bounded same-SID result
inventory. A selected genuine INTEGER callback matches the full closed original
workflow_start command and its source tuple/INITIAL source work radius. A null selected result
never proves original absence or excludes later callbacks. No old active tracker
binding, current assignment equality, claim consumption or source grant follows.

Caller first admits complete native/J structure and holds one coherent raw-value
connection/transaction/lock. Fixed parameterized SELECTs only; no transaction
control/await/DTO/default counter/converter. Original typeof and byte lengths gate
before fetch, including multibyte/NUL suffixes. Every new native row gates ALL
variable bytes against remaining budget before payload fetch; singleton keyset
pages renew that gate. A bounded uniqueness probe prevents task LIMIT1 masking.
All fetched candidates, including rejected/overflow rows, charge one8192-row/
16384-step/16MiB raw-byte budget; per-value1MiB, P64/setup1024/source20.
Native nullable/empty/REAL accounting is separate from mandatory setup/admission
validation. Limits do not claim total SQLite/process/decoded-copy memory bounds.
Failures are category-only coding_source_history_invalid with no partial return.

Original callback C0 and first claim C1 require independently admitted captures;
only their exact25 named load-bearing columns may agree with separately retained
timestamp observations at that initial boundary. ALL27 C1=C2 equality is mandatory
after preflight; intervening heartbeat refuses without any partial coding write,
debit or publication. Full27 attributed replay/version-aware heartbeat handoff
remain mandatory after bootstrap. These capture/writer/replay obligations remain
UNIMPLEMENTED here. Fixed callback/source captures plus independent historical
consumer, complete J/native/current-policy/invocation/Pause/move/host/recovery/
cutover closure must precede EVERY producer. No capture/native writer/mount,
authority inference or live operation is supplied. Existing F–J/refusal/create-org
I/document history, disabled K0 and unavailable K1 remain. FIRST fresh FULL
reviewer then same-final-head scoped SOURCE QA then SAME ROOT10693; static-only
local all/hosted lint, normal hooks/nonforce push and guarded manager merge.
Python units RETIRED, integration INCLUDING callback SKIPPED24342; all SQL/parser/
product/native/security/negative/mutation/repetition/executable proof remains
UNEXECUTED/FOUNDER-WAIVED139429/430. Root retains K2a2–K6/operational acceptance.

Native result `learnings` is nullable free TEXT, inserted unchanged by its owner:
NULL, empty, prose and UTF8 bytes remain exact in EVERY same-SID result. Only
owner-serialized JSON columns undergo JSON validation; all native storage/UTF8/
prefetch per-value and shared-byte gates remain. The selected allocating callback's
full original work repository/object-format/base/paths matches the INITIAL source
P exactly, taken from the first node of the already validated complete native
source-to-selected lineage. Same-issuer source refinements may narrow paths and
retain that original callback; the selected P and all parent attenuation checks
remain intact. A callback matching only a narrower child, conflicting identity,
missing native ancestor or widening refuses; no caller-picked ancestor, broad
subset substitute, extra query, historical rewrite or current grant follows.

### Original source-capture history — K2a2b5

ROOT10693 step63 / Founder139562/559/329 selects accepted TASK11484 appendix
4ee1d1819d3b58ff1350b192d6438fa8ed8bf99ba7cc06abfb0fe315fb4312e1 and cases
f55f71a1db09cf708dbad083cd8c955cd5241cf5f244586eaa3b19d7909c24ae after DESIGN11485.
Unmounted `coding_source_admission_history.read_source_admission_history` consumes
the fixed `coding_source_admission` / `workflow-coding-source-admission@1` audits.
It returns immutable original source/callback captures, explicit INITIAL native P
and native identity links, and allocating full-decision fingerprint. Selection by
authorization_id keeps the complete selected attenuation but never substitutes a
refinement's own mint admission or a mapping capture for the original source P.
The existing five-field native identity API and its free-text learnings/INITIAL
work-radius behavior remain intact through private shared-budget composition.

Closed phase/owner/claim/budget and lossless typed27-task/18-result rows retain
NULL/empty UTF8 bytes, strict signed INTEGER and finite binary64 bits, complete
ordered original parent-to-NULL ancestry, original command and raw native audit
JSON bytes (which need not be canonical). Native immutable task identity/parent,
full result/decision and genuine unique manager_decision session-start links are
checked separately from today's mutable assignment/SID/counters. Allocating C0
and C1 compare exactly25 named load-bearing fields in every source/ancestor row;
both original updated_at/last_heartbeat observations remain independently valid
and retained. Nonallocating captured NULL means absence at that admitted boundary,
never absence of later callbacks or allocating permission. Source claim/invocation
ID/frozen limits are retained facts; actual invocation/Pause and policy proof is
still separate. Zero max_revise_rounds keeps its native meaning.

One coherent caller-owned raw SQLite transaction/lock; SELECT only. Complete
bounded fixed-action audit inventory exposes duplicate/conflicting/foreign owners
without JSON-selection predicates or latest-row fallback. Native typeof and each
row's remaining row/byte ceilings gate payloads BEFORE fetch. Shared8192 fetched
candidates/16384 traversal steps/16MiB byte budget includes conservative decoded
byte copies, with1MiB per value/whole raw audit, source20/P64/setup1024. These are
not total SQLite/process/JSON-object memory limits. Missing required captures
raise coding_source_capture_unavailable; invalid evidence raises category-only
coding_source_history_invalid, without private details or partial return.

This delivers the historical capture consumer only. Callback/source capture
WRITERS, direct mapping-history capture selection, C1=C2 ALL27 writer equality,
full J run_started/source-baseline/replay/native meaningful-writer and heartbeat
bridges, actual invocation/Pause/move/host/terminal/outer cleanup, historical and
current policy/source_rule/base/permission and recovery/cutover readers remain
mandatory BEFORE EVERY producer. No J validator, complete source authorization,
valid-run Boolean, native/public/CLI/TS mount or producer permission is supplied.
F–J/populated/reserved refusal including validate_data=False, create-org I,
document histories, disabled K0/unavailable K1 and protected owners stay unchanged.
FIRST independent FULL review, then same-final-head scoped SOURCE QA, then SAME
ROOT10693; static local all/Source Boundary Lint/Web Lint(Node24), normal hooks,
nonforce push and manager guarded merge remain. Python units RETIRED; integration
INCLUDING callback SKIPPED24342; SQL/parser/product/security/protected/executable
proof UNEXECUTED/FOUNDER-WAIVED139429/430. Root retains K2a2–K6, real-agent/two-org/
override/backup-copy operational acceptance. No live action or trial renewal.

## Literal text and recipient intentions (THR310 seq3/13)

All Founder-authenticated writes, including an unbound agent-launched CLI, reject
unknown/nonparticipant body intentions with422 and eligible current-name/ID guidance.
Genuine Former anywhere in To/body retains409 before body422; unknown explicit To
retains404 and unresolved aliases retain503 under naming failure. Preserve native
structural/auth/binding/lifecycle/file errors. To alone selects participants (plus
a genuine agent composer). BODY suggestions are eligible selected prospective
agents in a new thread, current participants in an existing one; Founder is human,
never an agent participant or BODY suggestion. Manual `@founder` remains valid.

Validated agent writers retain compatibility; CLI stderr describes ignored tokens
and canonical narrowed targets or conditional fallback: recipients minus speaker
outside an exchange, frozen cohort intersect recipients minus speaker inside one,
possibly empty. Prior catch-up is independent; pending_replies proves neither
audience nor storage/launches. Direct agent API/internal authors receive no warning
channel (accepted limitation). Complete valid JSON-only bindings count for CLI
warning/upload attribution; partial/invalid bindings retain native refusal. No
response warning field/header/auth change or preflight reservation is introduced.
Founder-only suppresses new selection while preserving obligations/prior catch-up;
Founder+unknown differs, and self-only/quote-only use ordinary conditional fallback.

Only start, finite whitespace, or `(` `[` `{` ASCII single/double quote opens an
unescaped ASCII token. Explicit physical lines with0–3 ASCII spaces then `>` are
literal; unprefixed lazy continuation and four-space indentation are outside this
quote rule. Top-level unclosed fences mask to EOF; quoted ticks/fences cannot own
later notes. The precise finite whitespace/code grammar and examples are in the
identity-names core spec's THR310 section; this is not a full Markdown parser.
Nonblank authored bodies remain exact through storage, including controls,
indentation, CRLF/bare CR and trailing whitespace. The existing strip emptiness
predicate and attachment-only null outcomes remain; subjects/history are unchanged.

Forwarders quote every physical source line/header/blank/marker. Generated source
separators may become LF; notes remain verbatim, including CLI note-file newlines.
CLI's fully serialized quoted excerpt is at most4096 UTF8 bytes, metadata/prefixes/
newlines/marker included; separator and note excluded. Reserve the quoted marker
and retain the longest valid source prefix. Web retains its last-message selection
and size policy. Raw occurrence ranges keep duplicates; admission spellings dedup.
Insertion replaces only the prefix through caret, preserving suffix/spaces/focus.
Thread-body rendering styles only proven exact raw intervals, noninteractively;
ambiguous transformations stay plain, code/Mermaid stay undecorated, task/KB defaults
and historical delivery stay unchanged. No render-time naming or recipient inference.
Shared data is protocol/thread-mention-corpus.json (51 lexical +13 write-path rows).
Public-unexposed mentions_json, authentic debt/outage and genuine task/dream/callback
proof require actual evidence and cannot be inferred from static/mock checks.


Current-main C3 composition preserves automatic job admission: omitted/false
review_required executes through native owner checks; true refuses before allocation,
legacy true pending is inert, and /run remains a tombstone. Authenticated later
prerequisite loss rechecks move/causal ownership in the native Pause transaction
before its failed-before-launch CAS, audit/journal COMMIT and genuine wake. Only
that exact completed no-launch refusal may settle; partial scratch/environment,
output or process effects and earlier uncertainty remain in producer history.
Naming keeps INSTALL-only N-SAC-1 structural admission and actual install-diagnostic
provenance alongside SAME native disposition, rollback and reconciliation rules;
no removed whole-release naming fingerprint is restored.

The scheduled daemon disk worker reserves a global native writer per admitted
org before spawn, through supervision/reap/report. Held/unowned orgs defer with
bounded codes in the existing summary/audit; other admitted orgs continue. On-mode
child admission and prune re-read the actual parent's active global writer ID
through strict append-only history and validate the optional move layout/hold.
An installed layout with missing/unknown ownership refuses before mutation; absent
layout preserves native behavior. Missing/incomplete effect closure, interruption
and partial mutation retain unknown writer evidence; a clean later provider result
cannot erase it. The common1800-second window includes reservation admission.
Off/default dry_run, metadata/process/age/containment safeguards and manual dry-run
remain; no cleanup/activation authority follows. These source changes have no
behavioral/migration/recovery proof; integration INCLUDING callback SKIPPED24342,
Python units RETIRED. That C3 checkpoint had no COMPLETE writer; current D/E/F
source and independent gates are described above. No live capability follows.
