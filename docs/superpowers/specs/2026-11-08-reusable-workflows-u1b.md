# Reusable workflows U1b source pointer

TASK-14509 under TASK-14506 / ROOT TASK-14478 implements the remaining pure
half of U1, authorized by Founder THR-336 message 85 / TASK-14461 revision 3.
The reviewed TASK-14397 technical specification remains authoritative where
revision 3 does not revise it. U1a shipped via PR1284; its design note is retained.

Current ABI and ownership: [reusable-workflows.md](../../agent-guides/contracts/reusable-workflows.md).
New sequential_reducer.py and sequential_briefs.py share U1a's compiler, finite
check registry and return-invalidation analysis. No production producer imports
them. Node/role/responsibility/outcome data drive every decision and brief.
The only behavioral U1a record adjustment allows bounded unknown result outcomes
to reach the reducer's explicit hold; declared definition outcomes remain closed.

No storage, allocation, service wiring, catalog admission, capability integration,
legacy progression, native enforcement or live operation is delivered. Four exact
example bytes and all protected comparison files remain unchanged. Out-of-tree
pure smoke uses synthetic adapter assertions; it cannot establish actual task
creation, callback authentication, native settlement, guarded merge or M1/V3.
Independent full review, separate scoped QA and manager guarded merge remain.
