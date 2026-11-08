# Sequential engine U3e-2: bounded caller return consumer

Source base: 43d33e8384460a27d0377fb71b7a4eed75c692fa. Authority: ROOT TASK-14640,
Founder THR-336 msg85 (TASK-14461 rev3) and msg102; TASK-14730 design sections
6/8/9/10.1, accepted D2/D4 and queue handoff/addendum 1. This append-only source
checkpoint introduces no start producer, installation or execution authority.

## Accepted contract

One immutable workflow return is U3c's `returned` event plus `run_return` outbox.
The existing complete independent reader admits history in a coherent snapshot;
bounded PK/index selection then identifies that exact run/source/carrier. A
completed run without return is ineligible. Reader failure/exhaustion has no
caller effect or ordinary fallback. Reader and selection scopes retain their
existing limits. Merged D2 separates A/B/C/L scopes; each is measured against
its own unchanged allowance, without cross-scope capacity pooling. The manager
scope-A observation ruling permits at most 4,608 schema/native rows and requires
constant A rows as history grows on the same schema. Other half-cap thresholds
remain. This changes no production cap; schema growth consumes A headroom.

Immediately after unchanged U3c return, the same terminal writer projects only
source/carrier block-kind clearance, final native task summary/directory and a
bounded run note. U3c exclusively owns lifecycle status/timestamps. Original
source INTEGER result/SID and all sequential history remain unchanged; carrier
has no result/SID. Only after COMMIT does notification rebuild native histories
and call the existing source parent helper, independently validating the return
again at that shared seam. All ordinary parent parked/delegated, lineage,
all-children-terminal and generation checks remain.

Explicit `queue=` selects the attachment queue for both `enqueue_if_absent`
reservation and generation-aware publication. `queue=None` preserves ordinary
selection and Coding's existing path. In nested `enqueue_parent_once`,
`selected_queue` captures selection at each call without rebinding outer queue;
both inner publishers forward outer `queue`. No proxy, orchestrator queue
mutation, nonlocal or Coding progression import is introduced.

An explicit sequential rediscovery branch admits returned-only obligations and
re-enters the same idempotent consumer. A crash before enqueue can leave a
replayable obligation. Queue insertion is not a durable acknowledgement or
exactly-once execution guarantee. Outbox remains immutable. Thread follow-up,
SYSTEM messages and invocation minting are not invoked, even for retained
partial thread fixtures. Native visit launch recovery stays held.

## Radius and cases

New `sequential_return.py`; only `_advance` caller projection/postcommit tail,
`_enqueue_task_generation_aware` accepted queue signature/selection,
`_enqueue_parent_if_waiting` accepted sequential branch/queue handoff,
`Orchestrator._rediscover_returned` sequential branch, and
`recovery.rediscover_returned` explicit owner branch change. Three maintained
contract guides receive U3e-2 sections. Bundled start-task remains byte-identical:
there is no agent command change. All other TASK-14730 protected hashes and
shared-file functions remain unchanged.

C8: engine-built returned vs completed-without-return, exact native identities,
no duplicate workflow return/result/SID. C9: repeated notify and rediscovery,
parked/sibling/generation gates, supplied/default queues and thread-not-invoked
boundaries. C10: scope/AST/compile/protected hashes and documentation parity.
Observation is limited to synthetic outside-passwd-home SQLite under the
accepted /var/tmp ruling, without daemon/worker/host execution. Seeded native
rows are fixtures, never authentic callback/host/restart proof. Actual results,
SB-5 counters and hashes belong to TASK-14744 evidence, not this design record.

## Verification limits

Required automated checks are Source Boundary Lint and Web Lint (Node 24),
static only. Python units are RETIRED; general integration is SKIPPED under
THR-243 seq42, never PASS; Product E2E is not triggered. No repository test source
is added. Independent full review, scoped QA and manager guarded merge remain.

UNVERIFIED: genuine manager callback/start admission, original host/outer
settlement before first launch, actual launch/PID/callback/terminal order,
genuine successful Coding and Draft/Review returns, concurrent callback/cancel
races, service restart/caller execution, venue generation races, V3/V3-0b,
access/capability witnesses, live installation/settings/activation and V5/M4.
U5 successful-output refusal and original ownership uncertainty remain. No
schema/format/reader/outbox/auth/permission/host/notification/dependency/hook
change, U4-U9, PR1252 or D2 reader work is included.
