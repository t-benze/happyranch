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
| Original evidence before Burn | `home_retirement_journal` preserves closed original service/private/managed/auxiliary identities with exclusive publication, sync and readback. Fresh loads retain those observations, rather than recapturing partially cleaned or replaced members. Active preparation in `home_retirement_coordinator` joins this inventory with the original serving peer and managed-native journal before real revoke. This is evidence, not an effect acknowledgement; the model still abstracts file custody and durability. |
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

The active coordinator now passes its real live cutoff receipt and saved
controller observation into controller stop/disable. Each native effect follows
a fresh receipt check and controller re-observation after that blocking check.
Managed stop/disable also consumes a fresh per-effect guard, joining the actual
live receipt or existing independent original-death/denial/quiescence proof to
each native effect. Its stopped readback is not a retired receipt. Selective
cleanup, account disposition and final publication remain unfinished.
The stopped coordinator can additionally hold the exact original issuer and
minter writer lock descriptions through `held_operations`, with original-member
and native/cutoff readback. Absent domains are not created or declared locked.
This supplies operation exclusion only; the model's Remove action still needs
the full selective cleanup/recovery owner. Private-leaf removal is now joined
to the stopped/cutoff and held-operation guards through `remove_private`. It
checks saved original leaves/ancestors before each effect, synchronizes and
reads back absence, and explicit reuse skips absent originals. Managed/native
cleanup, account disposition and final publication remain unfinished.
Empty service-owned directory leaves are opened relative to their pinned parent
and compared by descriptor/name to the original custody; they are not inferred
to be root-owned ancestors. The model abstracts this no-follow custody proof.
`remove_auxiliary` now supplies partial issuer/minter leaf cleanup with fresh
stopped/cutoff proof and held original serialization domains before every
unlink. Original lock files/directories remain pinned and retained; fresh
cleanup contexts reconcile absent original non-lock leaves without recapture.
Closed directory names, full remaining-leaf identity, parent sync and absence
readback are real-code obligations abstracted by Remove. Lock-domain removal,
managed/native cleanup and final completion remain unfinished. Private cleanup
now uses explicit cleanup-mode exclusion so fresh saved recovery can repeat both
phases in either order, retaining original lock domains. Already absent original
leaves still require synchronization of their original parents and absent-name
readback; Remove abstracts those durability operations rather than treating
absence as an acknowledged earlier sync.
`remove_managed` now applies the same Remove obligations to the saved original
managed non-trust leaves, with shared-account parent custody, immutable config
signatures and runtime inode/custody continuity. Closed runtime names, fresh
stopped/denial/held-operation guards and explicit absent-parent sync/readback
remain real-code obligations. The trust pair/lock, native artifacts and all
serialization domains stay retained for independent recovery. Full removal,
account disposition and final publication still require their own owners.

`home_retirement_inert.InertRetirement` separately covers authenticated
prepared-inert native disable. Original and current activation, live binding,
trust pair/lock, connector sockets and saved serving peer must be absent;
the dedicated controller and every fixed managed native owner must be freshly
stopped/process-empty. Complete original journals are reused, and an absent
native journal can receive its first publication before effects. Native
readback skips completed disables after interruption; original members and
quiescence are checked again before each remaining effect. Active/unknown
evidence cannot enter this path. Cleanup, account disposition, final receipt
and pre-unpack integration remain unfinished.

The model's additional inert initial branch abstracts these independently
proven empty authority facts as already satisfied Denied/Closed predicates.
Stop still requires fencing, explicit selection, ownership and fresh cutoff
observation; no active-to-inert action exists. This is a vacuous empty-set
mapping, not a claim that inert code performs revocation or proves death of an
active owner. Custody, inert provenance and native absence remain real-code
obligations. These initial-state changes have not been executed with TLC.

Explicit `ActiveRetirement.from_saved` covers the pre-Burn preparation
interruption with a complete original inventory and saved peer. It rechecks
original members/native ownership and the same live peer before finishing an
absent native journal or synchronizing a complete existing one. Missing/torn
inventory/peer or post-revoke drift stays pending without recapture; the model
abstracts this original-evidence durability and does not prove the I/O path.

`ActiveRetirement.resume_revoke` additionally covers partial Burn/Deny while
the saved original serving owner is still live. Current anchored trust is parsed
by the retained real pair owner, while every other original member, trust lock
and native identity stay pinned. Only a new actual original-registry physical
acknowledgement admits the existing native-stop sequence; a failed original close
is never reset. This implements an R02.b prerequisite, not final completion or
an alternative death proof.

`ActiveRetirement.resume_controller` covers interrupted Stop/Disable of the
controller while the original managed serving owner remains live. Fresh real
acknowledgement and original managed identity precede each remaining effect;
native readback requires the controller stopped and skips completed disable.
Before-stop recovery uses `resume_revoke`. Only the disabled original
link and stopped controller runtime contents/absence can differ, with present
inode/custody and immutable originals retained. This is an R02.d prerequisite,
not full cleanup/publication or a substitute for independent managed death.
The same entry also covers partial target/sidecar Stop with that original
connector still live. Each completed effect is skipped from current readback;
only independently stopped/process-empty sidecar runtime may flush/disappear,
with original surviving inode/custody retained. Active runtime/configuration
and unknown runtime members still refuse. Dead-peer partial-native recovery
remains outside this entry; the model abstracts Stop rather than these native
substeps and is not execution evidence for them.

Fresh explicit `StoppedRetirement.from_saved` objects can now reopen the
already stopped/disabled phase using all three saved original records, exact
original native artifacts and independent death/denial/quiescence proof. They
perform no IPC or native replay and produce no final retirement record. This
covers one post-native recovery boundary, not every modeled Crash phase.
`resume_native` additionally finishes remaining owned native effects after
independent original death, anchored denial and complete managed quiescence.
Every effect keeps original identity and the observed current boot pinned;
actual native readback skips completed effects. This covers interrupted
disable/lost-reply progress, while selective cleanup and publication recovery
remain unfinished. The dedicated owner also pins its original still-present
activation link across stop and disable.

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
