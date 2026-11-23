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

## Slice B lifecycle and quota chunk companions

`LifecycleSweep.tla` models one artifact with expiry at day1, actual expiry
transition plus7 full recovery days, explicit operator approval (all rollout
gates abstracted together), preview/off/enforce, live reference arrival/removal,
identity replacement, committed delete intent, final writer fence, unlink,
crash before tombstone, and missing-byte restart reconciliation. `Expire` maps
to A's lifecycle transition; `Prepare` to A's durable delete intent;
`Fence/Unlink/Finalize/RecoverMissing` to B's `finish_delete`. Reference arrival
serializes outside the final writer fence. A byte effect without a final row
remains explainable by its issued intent.

`QuotaChunks.tla` models two crossing identities, three ordered chunks each,
transaction-local staging, all-message/receipt/audit/latch COMMIT, crash rollback,
rearm below threshold and stable sequence replay. These map to B's quota notice
builder and A's `shared_artifact_quota_alert_uncommitted`, inside the same
transaction as `quota_crossing_uncommitted`. No per-chunk receipt key or new
schema is modeled or implemented.

Using the same pinned jar, Java21, workers1/fp0/seed1, run from this folder:

```sh
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir /absolute/task-scratch/sweep -config sweep.cfg LifecycleSweep
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir /absolute/task-scratch/quota -config quota-chunks.cfg QuotaChunks
```

TASK-18791 actual TLC2.19 results, 2026-11-23 (each under1s):

| Config | Exit | Result |
| --- | --- | --- |
| sweep.cfg |0| No error;22,950 generated /3,075 distinct, queue0, depth25. TypeOK, NoLiveUnlink, FullRecovery, ExplainableBytes and TombstoneAudit. |
| negative-fence.cfg |12 expected| NoLiveUnlink counterexample: reference arrives between final proof and unlink when writer exclusion is deliberately broken;8,306 generated /1,111 distinct, depth16. |
| quota-chunks.cfg |0| No error;63 generated /23 distinct, queue0, depth14. TypeOK, AllOrNothing, OneAudit and StableReplay. |
| negative-quota-chunks.cfg |12 expected| AllOrNothing counterexample after first chunk becomes visible before receipt/audit COMMIT;4 generated /3 distinct, depth3. |

The negative configs demonstrate sensitivity, never passing production modes.
The models establish bounded safety only. They do not prove Python refinement,
scanner completeness, Q3 reader exclusion, real inode/hash custody, cancellation,
clock/DST implementation, quotas in real bytes, liveness or deployed activation.
Those remain private venue/independent QA obligations. Crash may retain an issued
intent indefinitely while preview/off, a new reference or uncertain identity
holds it; no unconditional reclamation is claimed. Legacy is outside modeled
destructive eligibility. Required checks and workflows remain unchanged.
