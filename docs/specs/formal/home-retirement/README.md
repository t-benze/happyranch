# Home retirement state transitions

Finite companion to the accepted THR-378 P24 case design (TASK-18796), covering
U02/U03, U04/O03, R01–R04 and the incoming Debian pre-unpack gate P02.
Two device grants and a separately tracked removed-device owner distinguish
burn, partial device commits, wrapper sealing/in-flight close, terminal failure
and acknowledged physical cleanup. Four fixed cleanup members abstract the
database, private keys, unit and activation material; they are not a filesystem
manifest or deletion authority.

| Model boundary | Production boundary / obligation |
| --- | --- |
| Fence | Original installer-directory flock and persistent historical pending-name refusal in `home_retirement_fence`. |
| Original evidence before Burn | `home_retirement_journal` preserves closed original service/private/managed/auxiliary identities with exclusive publication, sync and readback. Fresh loads retain those observations, rather than recapturing partially cleaned or replaced members. This is evidence, not an effect acknowledgement; the model still abstracts file custody and durability. |
| Burn / Deny / BeginClose / AckClose / FailClose | Actual serving `PinnedPairingManager.revoke_all`, anchored per-device transactions and original `StreamRegistry`, consumed through `home_retirement_cutoff`. Partial commits survive failure. A fresh registry cannot reset a failed close. |
| OriginalDies | Independent original-process death and transport quiescence proof, **not** a successful response from a replacement process. `home_retirement_recovery` supplies this read-only proof only with original peer/native binding, anchored denial and all managed units/processes independently quiescent; full cleanup/final-receipt composition remains unfinished. |
| ObserveCutoff | Original-owner physical acknowledgement or independent original-death/managed-quiescence proof, plus anchored burn/deny readback. No Boolean or phase intent substitutes for these. |
| Stop / ObserveStop / Disable / ObserveDisable | Fresh exact native/fragment/process/link ownership and stopped/disabled observations in `home_retirement_service` and `home_retirement_transport`; effect and observation are separate crash boundaries. Original managed observations have a separate durable transport journal; the full coordinator still needs to consume all pinned evidence before effects. |
| Remove / AccountDisposition / ObserveCleanup | Accepted selective no-follow cleanup and exclusive-account deletion or proven owned-disabled retention. Full coordinator/cleanup remains to be implemented. |
| WriteReceipt / SyncReceipt / ReadFinal | Accepted minimized completion publication, file/parent sync and fresh readback; never seeded success. Full writer remains to be implemented. |
| GateUnpack / ReplacePayload / RestoreEntry | Accepted incoming canonical preinst before old-payload replacement, trusted recovery capability and partial replacement. Package entry remains to be implemented. |
| Crash / InterruptWrite / Detect / SelectExplicit | Durable denies/effects survive; detection has no destructive action; only explicit recovery can progress. |
| ReplaceForeign / RollbackArtifact | Sampled ownership may change before the next effect; foreign/reappearing artifacts stay pending without stop/disable/removal or restored authority. |
| OldAdmission / LaterAccountAuthority / AccountListen | Fence rejects old admission. A hypothetical later listener needs separate account authority plus confirmed retirement; this represents S02's future gate, not a positive account path on main. |

The model is an accepted-contract abstraction. It is **not a refinement proof**
for the unfinished coordinator, a claim that all 23 parent cases pass, or proof
of installed dpkg/systemd/network behavior. No secret or user data is modeled.
Filesystem custody, NSS exclusivity, package digest provenance, original-peer
identity, callbacks, native sampling and fsync behavior remain real-code/test
obligations. `torn` denotes a positively owned interrupted completion write;
unknown/foreign pending bytes have no enabled publication-recovery action.

`current.cfg` checks safety and monotone persistent denial, fenced old admission,
the later-account gate and the absence of startup destruction. Stop and disable
need fresh ownership separately. Detection cannot perform those actions, and
already completed individual effects are never repeated. `completion.cfg`
adds **conditional** completion: after explicit recovery is repeatedly
available, exact ownership and the trusted entry remain stable, physical
cleanup can finish (or original-process death is independently established),
and progress is weakly fair, recovery eventually reports retired. There is no
completion promise for foreign ownership, unavailable entry, permanent failed
live cleanup, endless crashes/replacement, or an environment withholding proof.
`negative-stop.cfg` deliberately omits fresh ownership admission and should
violate `NoForeignEffects`; it is a sensitivity configuration, not production.

Use the existing official v1.7.4 toolchain documented in the
[task-lifecycle model](../task-lifecycle/README.md#run-tlc), SHA256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`.
From this directory, with task-private scratch:

```sh
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -metadir /absolute/task-scratch/tlc-home-safety -config current.cfg HomeRetirement.tla
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -metadir /absolute/task-scratch/tlc-home-completion -config completion.cfg HomeRetirement.tla
java -XX:+UseParallelGC -Xmx512m -cp /absolute/tla2tools.jar tlc2.TLC -workers 1 -metadir /absolute/task-scratch/tlc-home-negative -config negative-stop.cfg HomeRetirement.tla
```

TLC parsing/model execution: **NOT RUN** in this source checkpoint. State
counts, invariant success, liveness and sensitivity are unverified; a command
listing is not evidence of their result. No CI trigger or dependency is added.
Model checks are review evidence only; required checks remain lint/static.
