# Replacement closure and returned workers

P24 source-status amendment: the replacement journal replay/reset owner and its
algorithm-specific tests are retired. Intent/Reload/Reset/Retire/Unlink/Crash
and their production mappings below describe historical THR-327 behavior;
ordinary current inventory refuses legacy transactions and requires absent units
to be inactive. The independent returned-worker Lock/Unlock/Settle behavior
remains current in `RemoteAccessHelper._run_lifecycle`, exercised through
Disable by `test_thr327_returned_worker_does_not_seal_slot`. The historical
model, configurations and recorded TLC results are preserved; this amendment
claims no new TLC run or refinement proof.

THR-327 / TASK-18883 companion for the existing replacement journal and helper
slot. Both v1 and v2 start at staged or already-reloaded reload intent. Reload
and reset can repeat after interruption; retirement requires inactive units.
Reset admission includes the fixed managed name, removed-unit phase, fresh
authorization/custody and absent/no-fragment/no-process/no-job shape. The model
abstracts those reads into Valid. It adds no journal phase or product dependency.

| Model | Production boundary |
| --- | --- |
| Intent / Reload / Reset | Installer _close_replacement; _reload_removed_units and _reload_unit_state; SystemdServiceManager.reset_failed uses the existing transport/timeout. |
| Retire / Unlink / RetiredInactive | Unchanged ordinary _inventory_units inactive gate before publishing replacement-retire-journal, then guarded journal unlink/fsync. |
| Crash | A new instance replays the persisted replacement-reload effect with all original custody/caller checks. |
| Lock / Unlock / Settle | Only the returned lifecycle worker waits for the RLock; main-context holders never join it. Completion failure retains uncertain, releases its capture and seals exit. |
| SettledReleased / NoSyntheticSuccess | A returned-and-settled worker cannot leave inflight busy or invent success after complete raises. |

WorkerSettles assumes the current lock holder eventually returns and fair
scheduling; it proves nothing about a still-blocked effect, audit flock, system
bus or arbitrary stuck main-context holder. The host's actual busy mechanism is
not proved. This is a finite model, not Python refinement or filesystem power-loss
proof. Existing deadlines, budgets and eight-call ceiling stay unchanged.

Use the official v1.7.4 jar and SHA256 pinned by
[task lifecycle](../task-lifecycle/README.md#run-tlc). From this directory:

```sh
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir /absolute/task-scratch/current -config current.cfg ReplacementClosure
```

Run negative-reset.cfg and negative-settlement.cfg with separate state folders.
They must fail ResetBound and SettledReleased respectively (expected exit12).
Actual commands/results are recorded in the task handoff. Model checks remain
review evidence, outside lint/static CI.

TASK-18883, TLC2.19 / OpenJDK21.0.13.1, workers1/fp0/seed1:

| Config | Exit | Result |
| --- | --- | --- |
| current.cfg | 0 | No error; 6796 generated / 3240 distinct, queue0, depth5; safety invariants and WorkerSettles pass. |
| negative-reset.cfg | 12 expected | ResetBound counterexample; 2265 generated / 1761 distinct. |
| negative-settlement.cfg | 12 expected | SettledReleased counterexample; 4226 generated / 2569 distinct. |
