# Profile remove adoption

Bounded companion for TASK-18833 / THR-396. Two consumers in two captured orgs
start unbound after a remove, with their original prepared publication bindings.
A valid explicit rebind resumes this ownership. Moves discharge one requirement;
reconnect discharges the remaining requirements; publication waits for the whole
captured closure. Crash drops process-local ownership, retaining durable identity.
TASK-18868 extends this companion with cold-start classification: matching forward
remove evidence permits explicit adoption while unrelated, ordinary-journal,
mixed-profile, partially-forward, active, live and scoped evidence holds it.
Classification and adoption preserve the native record's unknown state.
TASK-18959 extends classification through finalization: matching published remove
and latest installed journal evidence with nothing active also permits startup.
No prepared fence remains, so this case enables neither adoption nor deferral.
Evidence classes include ambiguous removes, mismatched invocation or operation
state, and an ordinary latest installed journal; they retain their holds.

| Action / invariant | Production boundary |
| --- | --- |
| Adopt / OriginalOwner | `_rebind_adoption`, `_validate_adoption`, `profile_change_interval(resume=True)` and `_validate_profile_binding`: same complete remove invocation, original ID/kind/members/generation, existing prepared journal. |
| Move / Reconnect | Consumer canonical segment plus `rebind_consumer`; ordinary/claimed profile writer with the existing validated adapter binder. |
| Publish / ClosureFence | `_finalize` and `_republish` retain every prepared journal while any captured org has incoherent dependencies. |
| CurrentRoster | Consumer completion captures the changed canonical roster outside leases, validates its bookends, and passes it to bound publication; startup recovery also uses its captured roster. |
| Classify / StartupIsolation / EligibleStartup | `startup_forward_remove_profile` checks local unknown remove evidence. The forward case retains the original prepared journal, complete-group `_rebind_adoption` and deferred synchronization. With nothing active, finalized evidence requires a single published remove matching the latest namespace-installed coordinator journal and exactly one remove in the org; it needs no adoption or deferral. Other native ownership still holds. |
| UnknownPreserved | Neither startup exception writes or settles the historical native record. |
| Crash | Process-local interval ownership and startup classification are lost. Durable operation, binding and native evidence remain. |

The fixed operation ID/kind/generation/member set are abstracted into the
constant original-remove binding. This checks finite safety, not Python
refinement, actual flock exclusion, native-writer settlement, invalid-request
validation, filesystem/SQLite crash atomicity or unconditional liveness. Partial
file-phase publication and its original recovery owner remain the existing
publication protocol's responsibility. The finite evidence classes abstract the
Python predicates and cross-org checks. All-published phase abstracts the matching
published operation and installed journal together; operation/journal mismatches
and multiple removes are ineligible evidence classes. The model does not prove
SQL row selection or writer-to-operation binding. The finite model reaches
finalized startup through Publish, Crash and Classify; the standalone restart
regressions exercise those actual boundaries. A retained startup hold is not an
enabled adoption. No runtime dependency, CI gate or scheduled run is added.

Use the official v1.7.4 jar pinned in
[task lifecycle](../task-lifecycle/README.md#run-tlc), SHA256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.
From this directory, with task scratch outside the repo:

```sh
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir /absolute/task-scratch/current -config current.cfg ProfileRebind
```

Run `negative-owner.cfg` and `negative-closure.cfg` with distinct state folders
to demonstrate sensitivity to replacing ownership and publishing only a locally
coherent subset. Those intentionally broken modes must violate OriginalOwner
and ClosureFence respectively; they are never passing production modes.
`negative-startup.cfg` deliberately exempts every native evidence class and must
violate StartupIsolation at Classify.

Actual TLC2.19 / OpenJDK21 results, 2026-11-24, workers1/fp0/seed1:

| Config | Exit | Result |
| --- | --- | --- |
| current.cfg | 0 | No error; 164 generated / 87 distinct, queue0, depth8. TypeOK, OriginalOwner, ClosureFence, CurrentRoster, StartupIsolation, EligibleStartup, UnknownPreserved. |
| negative-owner.cfg | 12 expected | OriginalOwner counterexample at Adopt, depth3; 25 generated/distinct. |
| negative-closure.cfg | 12 expected | ClosureFence counterexample after Classify, Adopt, one consumer Move and Publish of that consumer's org, depth5; 51 generated / 37 distinct. |
| negative-startup.cfg | 12 expected | StartupIsolation counterexample at Classify, depth2; 14 generated/distinct. |

These are bounded review evidence, outside required CI.
