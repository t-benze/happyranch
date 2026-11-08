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

TASK-14576 / root TASK-14563 adds the U2a readers-only composition on fresh main,
including the manager-named additive Coding reference-reader recognition. Full
sequential validation precedes an explicit native-snapshot scope that retains all
original recursive document/Coding data checks and complete combined references.
Exact sequential table names and complete typed-row equality enroll new metadata;
non-sequential discriminators remain unchanged. Installed v2 scalar recognition
holds new marks for the future common engine; v1 behavior is retained. The current
maintained guide owns this extension. Historical descriptions above retain their
original-head meanings. U2b/V3, live operations, U3 producers and other holds remain
separate; disposable reader observations are not behavioral acceptance.

TASK-14576 also keeps the retained reader's narrower per-call budget separate
from the wider schema/copy counters, adding its consumption to the unchanged outer
totals before success. This corrects empty combined Coding refusal without changing
any retained budget guard/cap; prior seeded-counter evidence above is historical.
