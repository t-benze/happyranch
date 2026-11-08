# Sequential storage U2a source boundary

TASK-14536 / root TASK-14531 implements the canonical records and standalone
reader portion of U2 from Founder THR336 msg85-approved TASK14461 revision3.
The authoritative unchanged storage/ownership/compatibility specification is
TASK14397 reviewed-baseline/technical-spec.md §8. This append-only pointer does
not supersede that specification.

Current source contract: [reusable workflow storage](../../agent-guides/contracts/reusable-workflows.md#u2a-storage-and-standalone-reader-boundary).
Installation/backout: [UNVERIFIED draft](../../runbooks/sequential-workflow-storage.md).
No production importer or writer is attached. Complete combined old-history
integration remains a follow-up U2a boundary; U2b installation and V3 have not
run. U3 source adoption and all U4–U9 holds remain separate.

TASK-14552 fixes TASK14548-F1/F2/F3 within the same source boundary: the §4
org/principal/start-key invariant, the §8 deferred run-first bootstrap dependency,
and the §3 single cumulative bounded-reader allowance. The maintained guide
above owns the corrected contract. No existing DDL, startup path, frozen grammar,
producer or installation authority changes; structural observations do not
supply populated-history or native acceptance.

TASK-14565 corrects TASK14556-F1 on the same PR: legacy-only validation uses a
genuine retained `_Budget` seeded from the outer counters and the unchanged
native connection, then folds counters back before the outer success checks.
The same SQLite progress/deadline handler covers retained unbudgeted queries.
Retained byte acquisition uses its original per-call caps plus that shared bound,
not pre-reservation against the outer 64 MiB cap; unbudgeted retained query bytes
are not charged to that outer counter. Original guards and refusals are unchanged.
