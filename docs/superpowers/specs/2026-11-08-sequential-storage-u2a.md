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
