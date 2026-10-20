# Thread reply breaker persistence and compatibility substrate

> Test-policy amendment (THR-291 seq40): Python unit source and gates cited below
> are retired. Product invariants and acceptance obligations remain unchanged;
> replacement E2E coverage is PENDING. See [the coverage gap record](../../python-test-reset.md).

> Status: current
> Current Source: `runtime/infrastructure/database.py`, `runtime/infrastructure/db/reply_delivery.py`, `runtime/infrastructure/thread_delivery_release.py`, unmounted `runtime/daemon/routes/thread_delivery_releases.py`, dormant `cli/thread_delivery_release_requests.py`
> Notes: PR A defines persistence/compatibility; serial PR B activates runtime behavior without API/web expansion.

THR-200 PR A adds two SQLite tables and three indexes. One episode row is keyed
by `(thread_id, agent_name, executor_key)` and has a unique immutable
`episode_id`; receipts are keyed by immutable `invocation_token`. The episode
state vocabulary (`closed`, `open`, `probe`) and receipt vocabulary (`failure`,
`success`) are storage constraints for the serial runtime unit. No current
producer writes these rows in PR A and absence of an episode is represented by
no row.

The shipping `Database` initializer creates missing objects with additive
`CREATE ... IF NOT EXISTS` statements. It does not alter, drop, reinterpret, or
backfill any existing column. Genuine SQLite artifacts pinned to application
commits `e197b20` (pre-substrate) and `2c068bb` (first substrate), including an
interrupted first-table stage, prove forward creation, repair, representative
legacy-row preservation, and idempotent reopen. The rollback/read harness loads
and executes the actual pinned `e197b20` application `Database` module and
asserts its real `ThreadRecord` and `ThreadMessage` return contract.

This is a single-version rollout boundary: stop daemon admission before moving
between application versions. PR A does not authorize mixed-version writes or
claim that an older process understands new episode rows.

Serial PR B consumes this substrate. A `(thread, agent)` with
`required_through_seq > acknowledged_through_seq`, both ownership slots NULL,
and no episode row becomes cooldown-probe eligible after 15 minutes, and
must be excluded while it holds a `held` deferral in an `open` exchange. The
approved non-configurable threshold 3 and cooldown 900 seconds, structured final-provider-failure taxonomy, eviction
separation and count-once semantics are active. OPEN coalesces with no launch;
one durable HALF_OPEN probe succeeds closed/reset or fails back to a rearmed
15-minute cooldown. Breaker recovery never releases a held exchange. Receipts,
leases, settlement and redacted audits are idempotent across restart,
concurrency and stale callbacks. Every scheduler tick republishes a committed
pending probe token through a process queue whose deduplication ownership spans
queued and in-flight delivery. A normal worker return acknowledges ownership;
a pre-claim exception or cancellation releases it for a later tick, while the
durable claim still launches exactly once. Environment
and config input cannot alter policy or continuity across restart. PR B adds no
API/web projection or manual action.

Current scheduler (issue1019 / THR-295): delivery listing and mint use sequential
`await asyncio.to_thread` calls. Each synchronized call acquires/releases the
original DB RLock wholly in its worker; Database.close uses the same lock.
No orgs_lock hold, owned-worker drain or lifespan change is added. Removal can
close before a worker starts and cause a logged sweep error; publication to a
detached queue need not be consumed. Cancellation cannot stop running thread work;
a committed pending token is republished on a later attached tick. Unloaded orgs
require reattachment for recovery. Mint/claim/settlement/count-once/cooldown rules
are unchanged. Other synchronous DB callers and shutdown retain their limits.

C1a/C1b (THR317 seq25/34, ROOT12084) supplies private native exact-delivery
disposition source bound to the actual initial serving lifetime. The real
successful ASGI/listener adapter admits its dormant service, performing no
operation. No active route, CLI, scheduler or reaper applies a human override. A pending
started REPLY interruption preserves acknowledgement/required range and breaker
episode, receipts, failures, cooldown and existing HALF_OPEN lease, while one
transaction creates one covering native queued replacement and consumes only
legitimate released catch-up markers. Held/unsafe state refuses. Terminal sources
get no replacement. Recovery, final claim, and the separate probe republication/
mint path validate the same strict append-only companion/source/replacement
history, including saved-prefix drift and late original lifecycle revocation.
Raw unknown/process/effect evidence and retry budgets remain unchanged. Late
writes, overlapping work and repeated external effects remain explicit risks;
proof-backed release is unavailable. Old-reader/mixed-version operation and
downgrade after companion use are unsupported. C1-13 activation and
C2 task-tail are deferred; no historical recovery or live use follows. All native
SQL/lock/race/cancellation/crash/security behavior is UNEXECUTED; integration
SKIPPED24342, Python units RETIRED, required checks lint/static only.

C1-13 dormant adapter source adds an UNMOUNTED four-handler router and an
UNCALLED `threads.register_release` with capability/preview/apply/show handlers,
private request custody and four client methods. Existing `threads.register`,
app mounts and serving owner stay unchanged: no supported installed release
tool, external_adapters_available=false and proof_backed_available=false.
Future request metadata derives from native strict DTOs. Original streamed UTF8
JSON is limited to16KiB before normalization; query/execution bindings refuse.
Bounded32KiB closed results/errors preserve committed=false/null/true and
no-store even on dependency errors; no synthesized202 or absence on failed read.
Preview/full-command digests are distinct. Explicit four-risk confirmation and
positive no-follow same-UID0600 file/0700 private-directory fsync/readback/close
custody precede exact-byte POST. Failures preserve bytes/key. Transport loss
allows at most one same-key GET and never another POST/rekey/poll; show never
publishes. Shared bearer/same UID cannot establish exclusive human presence.
Disconnect leaves native lifetime/outer retirement with the existing owner.
Fixture/native-denial/mount/registration activation remains HELD: the six shared
Web/Swift reader cases lack attributable RED/restoration/GREEN, with no waiver
or execution allocated. C1-13 public delivery and C2 remain pending; all adapter
runtime/security/custody/HTTP/CLI proof is UNEXECUTED, never test-authoring PASS.

C1b registers each operation and org native lifetime before submission, captures
every returned future before a cancellable await and owns outer retirement after
disconnect. Positive native return, rollback/settings restoration and outer
settlement alone discharge that guard. Revoke closes loop intake first and takes
only its C leaf for at most25ms; timeout is incomplete and bars host shutdown,
detach and DB-close fallthrough. Remove/all-close/runtime switch/direct org close
independently require positive C idle and renewed attachment/busy/hold checks.
Final order is move -> DB -> controller -> residue -> tracker -> C leaf -> the
existing COMMIT. All SQL inventory/decoding finishes before owner guards; only
COMMIT occurs inside, with reverse release before rollback/readback/notification.
The original3s budget starts before submission, includes both observation passes
and native+SQL work within5M steps. Under actual owner locks, built-in maps over64
refuse before iteration; each pass reserves n+1 rows,2048*(n+1) raw bytes and4096
native units per record/completion. Any residue/unhealthy measurement, relevant
retained ownership/binding/preparation or outstanding native writer blocks.
Org-level unresolved move/Pause controls conservatively refuse; no task root is
inferred from thread text or tracker absence. Fresh guarded CURRENT facts and
existing admission/release counters detect observed changes, without certifying
invisible historical tracker/residue ABA or old process/effect retirement. After
commit, independent current eligibility permits only SAME-token put_once through
the existing queue; notification loss never rolls back, remints or reapplies.
