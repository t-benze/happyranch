# Shared Artifacts transition journal

Bounded Slice A companion to the retention design. Two generation slots share
capacity5 and size1..3. Time uses elapsed UTC hours at seven selected instants;
default grant2160h (90 days), reference maximum4320h (180), provisional timeout24h.
This abstracts real byte sizes while retaining admission and temporary overwrite
pressure. No CI workflow, scheduled run or runtime dependency is added.

| Action | Python boundary / assumption |
| --- | --- |
| Reserve / Stage / Commit | `SharedArtifactService._prepare_row`, staging identity/preparation, `_finalize_put`; metadata precedes physical bytes and full incoming staging counts. |
| CrashAndReconcile | Positive request-owned staging compensation / `reconcile_operation`; uncertain identity stays held in Python and is not an enabled destructive model action. |
| Bind / Tick | Atomic `bind_uncommitted` and upload-based `_timeout`; tick combines overdue timeout observation with its fixed decision, never shifts expiry to sweep time. |
| Commit second slot | Exact active overwrite with verified negative references; original generation row survives replacement. |
| Grant | Explicit renew/restore with continuing use, validated owner/reference reason and grant bound. |
| Download | Pure grant stutter; no implicit renewal. |

Reference/rollout proof, actor validation, hash/inode custody, SQLite COMMIT
failures and the actual seven-day restore deadline are Python/private-case
obligations. Recovery eligibility is abstracted as an enabled Restore; this
model does not prove the deadline or scanner. Custom-skill caller transactions
abstract their INSERT→blob→COMMIT/compensation sequence; a crash before their
SQL commit remains a held two-store uncertainty. No source-level refinement or
deployed enforcement is claimed. Immutable history and bounded grants are
safety invariants; no unconditional recovery/deletion liveness is promised.

Use Java and the official v1.7.4 `tla2tools.jar` pinned by the
[task-lifecycle model](../task-lifecycle/README.md#run-tlc) (SHA256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`). From this folder:

```sh
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -metadir /absolute/task-scratch/tlc-current -config current.cfg SharedArtifacts.tla
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -metadir /absolute/task-scratch/tlc-negative -config negative-reservation.cfg SharedArtifacts.tla
```

Actual review evidence, TASK-18746, TLC2.19 / OpenJDK21, 2026-11-23:

- `current.cfg`: exit0, no error; 958,467 generated /152,719 distinct states,
  queue0, depth17, 2 seconds. Checked TypeOK, NoBytesWithoutRow, CountedOnce,
  QuotaBound, BoundedExpiry, DownloadsNeverRenew and ReleasedOnRecovery.
- `negative-reservation.cfg`: expected exit12, `CountedOnce` violated at depth3
  after Reserve→Stage leaves a materialized byte plus its reservation. 15 states
  generated/distinct. This configuration demonstrates invariant sensitivity;
  it is deliberately not a production behavior or passing configuration.

Model checks are review evidence **only**, not a required check or CI gate.
