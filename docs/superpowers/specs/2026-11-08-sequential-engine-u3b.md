# U3b selected admission validator and inert first activation

Append-only source design pointer, TASK-14635 → TASK-14633 → ROOT TASK-14563.
Founder THR336 msg85 approves TASK14461 plan revision3 SOURCE; msg95 continues.
TASK14630 revision2 sections2/3/5 control the selected validator and supersede
TASK14615's full-composition proposal. That earlier design's radius, interface,
protected sources, production seam and doc parity remain applicable.

The implementation is exactly the U3b boundary in
[reusable-workflows](../../agent-guides/contracts/reusable-workflows.md#u3b-inert-admission-and-first-activation-thr336-msg8595).
The selected validator accepts only admission and has the `evidence_ids=()`
keyword for later PK-selected auxiliary evidence. U3c scopes are not implemented.
S1 copies `_candidate_structure` with its object query restricted to sequential
objects/ownership and its native branch removed. Temp-shadow refusal, bounded
row/type checks, settings per-row checks, selected ownership and native contract
checks retain parity. The reference is constructed from literal sequential DDL,
ownership@2 DDL and the document-letter/coding-specific binding-view literal.
No candidate SQL supplies reference DDL. All selected data reads use existing
indexes. No prior output content/result envelopes are acquired; no full layout,
settings generation chain, legacy history or ownership inventory is called.

The unchanged full reader is the offline oracle, including older-row tampering,
with the known TASK14631 small-org row/byte/work limits. Founder THR336 seq99
Decision2 remains open. Its module sentence “No normal startup, allocator, route
or producer imports this module” is stale after the validator's helper imports;
the protected source is deliberately unchanged. Admission-only validation does
not claim a complete historical scan or remove that oracle limitation.

The engine has no production caller. Genuine captured authority and a
caller-owned `admission_writer` surround all writes; no await/file/network or
COMMIT belongs inside the engine. All admission preconditions precede the first
INSERT. Transaction-local FK deferral covers run/carrier/source adoption/entry
visit/native mark/event/outbox/pointer construction. Selected validation is last;
exceptions propagate to the writer's rollback. Replay uses the stored start key,
exact canonical bytes and original source tuple to return original IDs; a
changed command refuses. No task identity overload, audit row, chain/fanout,
schema/cap/guard change, startup/route/callback attachment or launch is added.

Structural observations use only fresh task-owned disposable SQLite, genuine
Database/OrgState/authority capture→admission_writer and the merged explicit
installer. Coding v2 and Draft/Review use their declared max_visits16/max_events4096
and maximum escaped request field; small independent connections use the
unchanged reader. Negative admission, stale capture, byte conflict and mutable
row tampering must roll back without residue. Measured and computed budgets
must remain below half of each unchanged cap, including preflight overhead.
These observations and lint are not behavioral PASS. V3, live storage/installation,
callback/host/native settlement and ordinary-use acceptance remain UNVERIFIED.
U3c, U4–U9/M2–M3, F4, SB-1 and held PR1252 remain outside this unit.
