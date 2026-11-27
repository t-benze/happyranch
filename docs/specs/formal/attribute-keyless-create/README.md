# Keyless create allocation and acknowledgement

Models the THR-390 PR10 store writer and opted-in browser owner contract.
`N` concurrent requests share a stem and one atomic writer. Begin selects the
lowest free bounded key; commit retains it historically, rollback retains none.
An initial historical reservation represents archived/renamed-away ownership.
A separate other-tab create and later metadata edit cannot attribute our write.
Response loss can precede commit/rollback; delayed work can commit after a list
check or acknowledgement. Only each original response marks its request saved.

Unknown owners cannot send again. Successful Check list enables acknowledgement
without saved attribution; failed/cancelled reads preserve the hold. Acknowledge
removes only self. Late responses preserve acknowledged state. A fresh deliberate
create is a distinct owner, not a resend of the old request. Ordinary definite
failure retry and stable-ID PATCH comparison are outside this finite model.
Name-to-ASCII and 64-byte suffix arithmetic are concrete K01–K08 cases; keys in
the model are ordinals. Archive and rename retain the same historical set.

Safety invariants: `NoFalseSaved`, `NoResendFromUnknown`, `AckReleasesOnlySelf`,
`FailedReadbackKeepsHold`, `DistinctKeysNoHistoricalReuse`. Action audit records
let invariants inspect actual before/after ownership/history, rather than only
allowed labels. No eventual response, liveness, distributed idempotency or
installed-native claim follows. Deadlock checking is off for terminal states.

Run with the existing pinned [TLC venue](../task-lifecycle/README.md#run-tlc):

```sh
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/keyless-states" -config current.cfg AttributeKeylessCreate
```

Author result, 2026-11-27, TLC 2.19 / Java 21.0.13.1, official v1.7.4 jar
SHA-256 `936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`:
**exit 0**, all five invariants held; N=2, MaxKey=3 (including exhaustion),
19,432 generated / 4,798 distinct states, depth 17, zero queued states.
Initial author run exited 75 due to missing parentheses around a TLA+ IF
expression; corrected before this successful complete run. TLC is review
evidence, not a CI gate or behavioral execution of Python/React/native code.
