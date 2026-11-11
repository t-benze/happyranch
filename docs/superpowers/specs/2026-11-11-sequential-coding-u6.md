# Sequential Coding U6: cancellation and mixed-history closure

> Status: current design; U6a inert reader interface implemented
> Current Source: docs/agent-guides/contracts/reusable-workflows.md
> Notes: U6b cancellation/native-tail producers and attachment remain required.

THR-336 / ROOT TASK-15165; maker TASK-15287. Fetched base B is
`dc35c34ca907d6094cc79654f6f7b5be7476fe82`. Plan rev3 TASK-14461 §U6
and acceptance AC8/AC9/AC12, R-M2-1 TASK-15144 (“U6 unchanged”), PRD v1.0.4
Shared Artifact `product_lead-2026-11-11-workflow-mechanism-prd-v1.0.4.md`
SHA256 `96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`
and Founder msg131 ACCESS-1 govern. [U4](2026-11-11-sequential-coding-u4.md),
[U5](2026-11-11-sequential-coding-u5.md) and
[D2](2026-11-09-sequential-history-reader-d2.md) retain their owners.

## Split declared before editing

Deliver **U6a**, a SELECT-only mixed-history interface, before mounting any new
producer. **U6b remains implementation**, not a verification-only remainder:
compose the existing Founder task cancellation/cascade writer with mixed
ordinary/document/old-Coding/sequential owners and native root-tail consumers.
The existing ordinary-ancestor guard and direct selected-run cancellation stay
unchanged. Mixed cancellation still refuses; there is no new executable path.
U5b2b2 callback-observation validation/mount and U7 task-session transport remain
separate. Final success stays unavailable-output Held. No new endpoint, schema,
dependency, credential, authority/permission surface or live operation is needed.

## U6a interface and ownership

`read_sequential_history(..., task_id=...)` explicitly requests a
`MixedSequentialHistory` whose immutable `task_history` is a bounded native
structural closure. The ordinary call (without task_id) returns the original
`SequentialHistory` on its original path. No mounted caller supplies task_id.
`read_selected_mixed_history_uncommitted` requires the caller's original lock,
connection and transaction and uses the same complete oracle, not a weaker
incremental lifecycle check. No engine/cancellation/startup consumer is attached.

All original combined-schema, sequential replay, ownership, retained document
and old Coding validation finishes before the optional closure is acquired in
the SAME snapshot. The closure follows parent, child and workflow-member edges
to a fixed point, including completed/failed/cancelled/superseded intermediate
tasks and dependencies. It is an inventory, never the set of tasks permitted to
receive a live cancellation write. Cross-root workflow links retain all native
roots; sequential versus retained instance namespaces remain distinct even if
IDs have the same text. Parent cycles, missing parent/member tasks, missing or
foreign marks, dual ownership, invalid types/identities and excess membership
refuse without partial results. Revisit/chain/fanout JSON and native process/job
ownership are not interpreted by this structural interface; their existing
owners remain controlling, and U6b must compose them where required.

Native tasks retain all columns in the existing native task projection as exact
SQLite values in frozen tuples, including NULL, SID, PID, status, notes, budget
and revision fields. Callback references retain the original positive INTEGER
id/task/principal/SID/status. They never select a newest result, coerce a string
id or require today's roster/current SID to rewrite historical attribution.
`MixedTaskHistory.result` matches the exact quartet; it does not authenticate
callback payload, successful host/outer settlement, effective access or effects.
U5b2b2 must still read the original PK/full callback and its original proofs.
Callback payload text is deliberately neither decoded nor copied by U6a; every
retained graph and callback byte stays with its original complete reader/owner.

The optional closure charges the existing D2 C budget and SQL progress hook,
after retained L work has already charged C. A/B/C/L row/byte/step/SQL caps and
one original deadline remain unchanged. The 512-distinct-task ceiling is an
additional membership refusal, not a promise that arbitrary 512-task payloads
fit. Repeated reads/edges and rejected candidates consume their original work.
There is no budget initializer in the closure, retry, slicing fallback, cache,
transaction control, DML, current-authority or filesystem/network observation.

| Owner | Caller / preservation boundary |
| --- | --- |
| `sequential_history.read_sequential_history` optional task_id | Existing callers omit it; complete combined oracle then closure under original C. |
| `sequential_mixed_history.close_mixed_history_uncommitted` | Only explicit complete-reader composition; requires already validated full combined history and original allowance/hook. |
| `read_selected_mixed_history_uncommitted` | Unmounted selected interface, same full oracle/snapshot; no cancellation or callback entitlement. |
| Native task columns/types; `_bounded_rows`, `_structure_cells`, `_binding_owner` | Reuse exact retained projections, pre-fetch gates and ownership relation discriminator; no code moved. |
| Historical Coding cancellation/return and document readers | Byte-identical; no new family interpreter or fallback. |
| Dispatcher/engine/business-Held, U4/U5, Pause, task-cancel route, native root-tail | Byte-identical; U6b must wire its own coherent producers later. |

## U6b producer contract (still required)

A winning business stop must fence fresh visits, result advancing, publication
and Final action. Only an original already-COMMITTED exact callback may drain
unchanged. Preserve original audit/CAS ordering; invoke opaque native Stop only
after COMMIT and outside locks. Cancellation never proves execution/effect
closure: retained prepared/running/unknown ownership, original local action and
receipt uncertainty, budgets, failed tasks and immutable returned/cancelled
histories remain with their original owners. Launcher TERM/INT, ended SID,
missing PID/control or a successful Stop request supplies no product cancellation
or native settlement proof. No Retry/Resume/Reassign/Merge-again/cleanup endpoint.

The next coherent producer unit must independently inventory each changed
entry/caller, use complete/selected mixed closure before writes, retain exact old
reader behavior, close deep terminal dependencies without mutating retained
graphs, and compose the native root-tail consumer. A partially wired producer
must remain unmounted. U6a alone does not discharge that work or release U5/U7.

## Evidence and preserved boundaries

Out-of-repo diagnostics use actual source readers and disposable copied SQLite
histories, with explicitly synthetic native/authority/effect scaffolding. They
are not V3/native acceptance. Handoff/PR record exact B/H, full file/AST and
caller inventory, protected-owner comparisons, zero-write observations,
commands/exits and the exact-head local-CI job receipt. No repository tests are
added. Python units RETIRED, integration SKIPPED THR-243seq42, Product E2E not
triggered; actual mounted mixed cancellation/native races, transport, callback
mount, V3/V5/M4, live access/merge and deployment remain NOT RUN.

Preserve TASK-14478/14531/14559/14572/14627/14632/14640/15049/15055/15074/15164
failures and TASK-15175/15246/15272 REQUEST_CHANGES history; no allowance renewal.
V3 ends 2026-11-23T08:48:38Z, extension Founder-reserved. Live config/admission/
roles/install/migration/deploy/restart/genuine Coding scratch/effect recovery
remain reserved; PR1252 held/untouched, notifications excluded. F-1=(i),
Decision1=B (48 KiB JSON), Decision2=1, SB-1=(a). ACCESS-1 is current authority
only in the daemon and selected-agent own live operations for effective access;
no daemon GitHub/network I/O or access observer. TASK-15250 Pause generation and
TASK-15275 receipt-recording uncertainty semantics remain unchanged.
Merged != deployed. Independent review/QA and manager guarded merge remain.
