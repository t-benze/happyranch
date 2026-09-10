# Product-design workflow U0 feasibility evidence

This U0 patch is deliberately non-production: it adds isolated proposed-schema and authority-limitation tests plus a frozen study preregistration. No route, daemon workflow, UI/CLI, migration, authority coordinator, or compatibility behavior is installed.

The executable fixture calls the actual `Database` initializer on an isolated
historical store, preserves that row, then uses an explicitly labelled test
adapter to install/reopen the proposed additive relations. It checks rollback,
replay, foreign keys and integrity. This is not a numbered production migration
or a claim that the adapter is a supported production migration path. The
authority tests call the actual `manage_agent(update)` route under its real
`teams_lock`: A→B→A demonstrates hash ABA, while same-hash contenders show the
route-local stale-writer rejection. No route-to-workflow transaction, dispatch,
or final join coordinator exists. A later D5 decision must authorize one before
execution workflows can proceed.

Study arms are prohibited until independent review locks the manifest; no arm
context, score, or measurement is included here. `u0_evidence_helpers.py`
freezes source bytes into a separate truth projection, renderable projection,
and protocol digest (three arms, fixed 18-rating denominator, tokenizer pin and
unselected model/identity). The maker cannot produce the detached lock.

The proposed join model is also isolated. It requires an idle caller connection,
then owns `BEGIN IMMEDIATE` before every authorizing read and rolls back its own
work on every rejection. It validates the specified instance/round/submission,
active authorization bound to the instance snapshot, reviewing lifecycle,
each Founder/implementation/test request and approved receipt (including
generation, scope digest, submitted digest and outcome), and the instance-wide
historical-contributor closure. One conditional terminal transition, one joined
event, and one replay row commit together. Same-key/same-body replay is allowed
only after that completed current instance has been revalidated; a different
body conflicts. The Stage-1 fixture uses non-default instance, revision and
assignment-generation values and deterministic independent-connection races.
It is a negative feasibility probe, not a production transaction or a claim
that SQLite constraints alone provide currentness, queue admission, authority
fencing, or cutover safety.

Stage-1 raw fixture digests: helper `68f60262bb8984a9f43d4cba013c0e72f0e68907377c677ff1f97b8b333b49d2`,
recovery test `b3823e42e6e62f5e9f4f6af788349101f92fd2424a4459d634636103305c7338`,
and proposed DDL `058b82675735d2ab6d63a97f9f118e8945876b81d6652d46a4be025033168c77`.
This digest record is proposal parity only and does not replace the withheld
study lock.

R1 remains an observed shipping limitation: `TaskQueue._worker_loop` dispatches
`run_step`, whose delegation calls self-committing `Database.try_delegate` then
queues a child; callback and cancellation are distinct routes. R2's historical
inventory pins the v0 DB-backed and v1 flat runtime initializer/layouts and
requires corrupt inputs to fail closed before the adapter. R5's decision packet
must still classify every writer/reader/compensation participant and receive
independent review before any protected D5 choice.
