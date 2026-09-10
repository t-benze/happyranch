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

The proposed join model is also isolated. Its one transaction owner reads every
current Founder/implementation/test receipt, checks each digest and assignment
generation, contributor closure and idempotency body identity before one event
effect. It is a negative feasibility probe, not a production transaction or a
claim that SQLite constraints alone provide currentness, queue admission,
authority fencing, or cutover safety.

R1 remains an observed shipping limitation: `TaskQueue._worker_loop` dispatches
`run_step`, whose delegation calls self-committing `Database.try_delegate` then
queues a child; callback and cancellation are distinct routes. R2's historical
inventory pins the v0 DB-backed and v1 flat runtime initializer/layouts and
requires corrupt inputs to fail closed before the adapter. R5's decision packet
must still classify every writer/reader/compensation participant and receive
independent review before any protected D5 choice.
