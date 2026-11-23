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
| Remove / AccountDisposition / ObserveCleanup | Selective original-member cleanup and explicit active/inert `retire_account` entries: fresh saved provenance, complete native/cleanup absence, bounded name/numeric NSS and consumer checks, exclusive user/group deletion or locked owned static-consumer retention. Final cleanup evidence disposition/completion remains unfinished. |
| WriteReceipt / SyncReceipt / ReadFinal | Publication in `home_retirement_publication` requires the absent or proven original retained-disabled account disposition, complete caller proof, canonical-prefix stage completion, staged file/parent sync, atomic no-replace rename and fresh readback; never seeded success. |
| GateUnpack / ReplacePayload / RestoreEntry | Incoming preinst sends exact retained canonical sources to the protected installed isolated CPython3.12 before old-payload replacement; only the canonical result permits unpack. Missing capability refuses. Partial replacement/recovery and installed acceptance remain separate. |
| Crash / InterruptWrite / Detect / SelectExplicit | Durable denies/effects survive; detection has no destructive action; only explicit recovery can progress. |
| ReplaceForeign / RollbackArtifact | Sampled ownership may change before the next effect; foreign/reappearing artifacts stay pending without stop/disable/removal or restored authority. |
| OldAdmission / LaterAccountAuthority / AccountListen | Fence rejects old admission. A hypothetical later listener needs separate account authority plus confirmed retirement; this represents S02's future gate, not a positive account path on main. |

New payload construction excludes the local Headscale, home CA and admin
executables and command-only source packages, and the Debian validator refuses
their stale paths. This supports GateUnpack/ReplacePayload without changing model actions: incoming preinst must
finish canonical retirement using the original installed capability first.
Retained client/shared transport and their notices remain. Synthetic package
checks do not establish installed dpkg or systemd acceptance.

F02's ordinary `prepare-headscale` and `disable-headscale` CLI adapters now
refuse before installer construction for both layouts, independently of saved
state. `test_home_retirement.py::test_retired_operator_commands_have_no_effect`
observes the exact copy, non-success, unchanged local/legacy files and zero native
effects. This stronger entry-specific refusal adds no model transition; the
OldAdmission action still abstracts legacy callers racing the retirement fence.
Other programmatic/home helper owner retirement remains unfinished, so this
mapping does not claim that every old admission path has been removed.

## Incremental implementation records

The records below retain the scope limits of each earlier implementation slice.
The boundary table above and final publication note describe current source;
no source description establishes unexecuted composed or installed acceptance.

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
readback are real-code obligations abstracted by Remove. The separate final
domain entry is mapped below; remaining managed/native cleanup and final
completion remain unfinished. Private cleanup
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
evidence cannot enter this path. Its explicit `remove_leaves`/`resume_cleanup`
also map to Remove: current stopped/disabled proof, original empty authority,
remaining-leaf custody and held original operation locks guard each selective
private/auxiliary/managed removal. Lost parent sync is reconciled without
unlink replay using the same originals. These entries retain native artifacts,
accounts, journals and lock domains. Separate removal entries are mapped below;
account disposition, final receipt and pre-unpack integration remain unfinished.
The stopped and inert `remove_private_directories` entries also map to Remove:
after all private leaves are absent, only the four fixed original empty private
roots may be rmdir'd under the same fresh proofs and held operation exclusion.
Original immediate-parent identity, parent sync and absent-name readback are
required even after interrupted removal. Missing/replaced parents, reappearing
roots/leaves and unknown/nonempty roots refuse; no recursive traversal or rmdir
replay is supplied. Managed stop confirmation retains original parent
device/inode/custody/mode through sibling removal, allowing only its resulting
link-count/timestamp changes; files/links and boot/native/process facts remain
exact. Native/trust/account/lock-domain and final publication
obligations remain separate; model actions and completion claims are unchanged.
The explicit stopped/inert `remove_managed_directories` entries apply the same
Remove obligations to the three original empty managed runtime/state roots.
Original non-trust leaves must already be absent. The denied connector trust
pair/lock and its directory remain for fresh independent cutoff readback;
native-cleanup observations allow composition after original unit removal.
Original immediate-parent identity, held operation exclusion, fresh active
death/denial or inert empty-authority proof, parent sync and absent-name
readback guard each effect and recovery without rmdir replay. Absent runtime
roots supply no death proof, and missing/replaced parents, new authority or
nonempty/replaced roots refuse. This uses the existing Remove abstraction;
trust/domain/account/native-final cleanup and publication remain unfinished.

The inert-only `remove_connector_directory` additionally maps Remove to the
original empty connector state root. Both saved never-activated/empty-trust
lineage and current empty authority remain mandatory; active trust becoming
absent supplies no inert transition. The real owner composes original-parent
identity, surviving held operation domains, current native quiescence, parent
sync and absent-name readback, including explicit lost-sync recovery and later
operation-domain cleanup without recreation or replay. Reappearing authority,
unknown/replaced roots and native activity refuse. This uses the same Remove
abstraction and changes no action, denial or final-completion predicate.

The active-only `remove_trust` entry maps the same Remove boundary to the
original completely denied trust triple and empty connector state root.
The real anchored-pair parser, under the original lock, supplies denial before
an exclusive identity-only intermediate record is synchronized/read back.
Remaining members stay pinned to those denied files during partial removal;
the model's monotone denial sets abstract that bridge, never a surviving
authority store. Every subsequent effect/recovery independently requires
original-peer death and complete current disabled/native quiescence; absence
or the saved record alone supplies neither. Original ancestry, held surviving
trust lock, parent sync, fresh absence and no removal replay remain real-code
obligations. Missing/unknown evidence, remaining-file replacement, reappearance
and native activity refuse. Original journals and account/final publication
remain separate obligations. No model action or completion predicate changes.

Explicit active/inert `retire_account` now maps AccountDisposition to the
protected original dedicated account after current complete cleanup absence.
Name and numeric NSS mappings, locked system shape, unshared UID/GID, bounded
UID process and static load-root consumer checks are real-code obligations.
Exclusive userdel precedes receipted unshared groupdel; failed/uncertain effects
remain pending and fresh explicit recovery skips only positively absent effects.
An exact locked owned account with static consumers is retained with a bounded
reason, without touching those consumers. Original journals/receipt survive.
Active independent death/native proof and the actual denied-state bridge, or
inert historical/current empty authority, remain required after user removal.
Ordinary observations stay strict; the explicit account-stage readback continues
to reject numeric reuse and native activity. This uses the existing exclusive/
owned-disabled branches. The same action now exposes user-absent before group
removal; crashes/detection preserve that pending state and fresh selected work
still needs CleanupFacts before completing group removal. No model action or
final-receipt predicate is added. TLC remains NOT RUN.

Explicit stopped/inert `remove_unit_fragments` now composes Remove for the six
fixed original systemd fragments, including fresh partial-removal/lost-parent-
sync recovery without native or unlink replay. Cleanup-aware native observations
permit original-fragment absence, with exact original cached inactive
definitions or not-found/empty definitions; remaining artifacts/parents,
accounts and complete current native quiescence stay required. Active cleanup
continues to require independent original-peer death and anchored denial;
inert cleanup retains original/current absent authority. Held original operation
domains guard each effect. Ordinary stop/disable stays strict. Binaries, trust,
accounts, lock domains and final publication remain separate obligations;
there is no new model action or final-completion claim. Installed native
behavior and TLC remain NOT RUN.

Explicit stopped/inert `remove_controller_executable` additionally maps Remove
to the one dedicated Headscale binary. The original private inventory captures
its fixed protected ancestors before effects; missing saved ancestry refuses
without recapture. The same current native quiescence, active independent
death/denial or inert empty authority and held operation domains guard removal.
Only cleanup observations admit the absent original binary; ordinary native
observations and stop/disable stay strict. Full remaining binary identity,
saved ancestry, parent sync and absent-name readback are real-code obligations;
fresh recovery reconciles a lost sync without unlink/native replay. Shared
interpreter/sidecar, registry, payload parents, trust, accounts and domains stay
retained. This uses the existing Remove abstraction and supplies no final
receipt or new completion claim; final cleanup and publication remain unfinished.

Stopped/inert `remove_operation_domains` also maps to the existing Remove
action. The original issuer file lock, two minter directory locks and empty
issuer/minter roots can be removed only after their non-lock leaves are gone.
Independent current native quiescence and active original-death/anchored-denial
or historical/current inert empty authority guard acquisition, every effect
and final readback. Surviving original descriptions remain held after unlink/
rmdir until scope exit. Only this explicit proof-bearing path admits prior
domain absence; ordinary exclusion stays strict and absence supplies no death
proof. Original ancestry, remaining-member custody, empty-root rmdir, parent
sync and fresh absence are real-code obligations. A removed parent's surviving
parent is synchronized during recovery without replaying child removal. Other
cleanup requiring those domains precedes this step. The trust pair, accounts
and original journals remain; final receipt/publication and pre-unpack sequence
remain unfinished. No new model action or completion claim is introduced.

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
absent native journal or synchronizing a complete existing one. Before the
first inventory publication, it additionally requires both journals absent,
intact historical lineage and current native PID/start/boot matching the saved
live peer. Any orphan native record, missing/torn peer, existing torn inventory
or post-preparation revoke drift stays pending without recapture. This is still
pre-Burn preparation, not a new model transition; the model abstracts original
evidence durability and does not prove the I/O path.

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

Explicit active/inert `remove_original_receipts` maps the final historical
account/publication/activation receipt leaves to `RemoveOriginalReceipt(r)`. The
saved inventory remains provenance after an individual receipt is absent;
complete current native/cutoff, other cleanup and final account readback guard
each effect. `require_retired` observes absent names or exact locked static
consumer retention without account effects. Ordinary account deletion remains
strict about the original receipt. Parent sync/absence recovery can finish
partial removal without native/account/IPC replay. Original recovery journals
and the old-payload fence survive; this is not `WriteReceipt`/`ReadFinal`, and
does not claim any completed R02→R03/R04 or package gate. The finite original
receipt set remains separate from earlier artifact removal, preserving receipts
through account effects. FinalFacts requires their complete removal; the
existing removal-authorization and no-startup-destruction properties include
this set. TLC execution remains NOT RUN.

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

The explicit active/inert `publish_final` entries join the completed historical
receipt absence to current account/native/cleanup proof before publication.
They may reload only the already-proven removed fragments, then require absent
service readback. Complete interrupted publication reuses the original staged
or final inode; no account/native-stop/revoke effect is repeated. The fixed
private recovery record family and permanent old-payload fence are retained
as inert provenance across unpack. The model abstracts their custody/durability.
Only a protected canonical-prefix staging inode can satisfy the modeled `torn`
state: explicit recovery rechecks full proof and named/open identity before
writing its missing suffix, and another short write stays pending. Unknown
bytes or unsafe/replaced stages have no recovery action. Detection performs no
repair, and published final inodes are never rewritten. The `owned-disabled`
account branch maps to the exact fixed retained-static-consumer disposition.
Its final detector requires read-only canonical original provenance, the saved
numeric identity, locked system-account shape, safe static consumers and no UID
processes. The model abstracts these readbacks as FinalFacts; it does not prove
their implementation. Existing R02.a-h obligations are not all established by
this slice, and the shipping preinst remains unfinished.

The canonical `home_retirement.main` entry now invokes
`home_retirement_upgrade.retire` under the original directory flock/fence.
Its ordering maps to the existing capture, revoke, native, removal, account and
publication actions. Read-only completed-prefix observations select the next
existing owner after interruption; they are not additional modeled authority
or a durable phase flag. Each owner rechecks its complete facts, and any failed
effect ends the invocation. The actual native/receipt interruption entry cases
remain separate from exhaustive R02 coverage and installed preinst acceptance.
No model action, state bound, configuration or TLC result changes here.

The expanded canonical-entry table observes original preparation beneath the
model's abstract Fence; it maps R02.b ceremony/partial-device commits to Burn
and Deny, R02.c failed closure to FailClose and OriginalDies, R02.d stop/disable
to Stop/Disable and their observations, R02.e fragment removal to Remove,
partial account removal to AccountDisposition, and R02.f/g to final
publication/readback. Unsafe original custody supplies an incomplete-proof
variant before explicit recovery; a live failed close is never reset by a fresh
registry. Fresh installer objects reuse real surviving files, and each completed
row must feed its produced receipt into R03/R04. These are concrete entry checks,
not additional modeled authority or a TLC result. R02.h/package replacement and
installed preinst acceptance remain separate unfinished work.

The fixed entry now constructs the retained `RetirementIO` adapter, sharing
existing filesystem/config custody checks while historical issuer/invite
readback avoids provisioning owners. This is a dependency boundary for the
incoming pre-unpack bundle, with no new modeled action or proof authority.
The generated incoming Debian preinst now carries a fixed retained-module closure
with byte parity against the new payload. Its closed inline loader cannot borrow
old runtime modules; the existing system interpreter supplies only the stdlib
bootstrap and canonical read-only fresh detection. Legacy upgrade requires the
protected fixed installed CPython3.12 capability and propagates its canonical
result. This maps to existing `available`/`Run` and `GateUnpack`, with no new
model action or proof authority. Source/control extraction and bootstrap refusal
checks do not establish active generated-entry compositions, payload-replacement
interruption, installed ordering or TLC execution.

The entry test table now joins actual package-extracted control/child bytes to
the existing active-fragment, active-partial-denial and inert-publication
interruption compositions.
Canonical owners, persistent revocation, original stream registry, cleanup and
produced receipt remain real; only the process/native/filesystem boundary is
adapted. Simulated isolated CPython3.12 capability is not installed execution.
The fresh inline inventory receives the native fixture's independent synthetic
released-binary pin; actual candidate digest verification remains in production.
The same rows map preinst success/readback to GateUnpack, partial retained-source
replacement with missing capability to ReplacePayload/Crash, restored capability
to RestoreEntry/Detect, and artifact rollback to RollbackArtifact. Abort and
completed readback have no replay. This adds no model action or proof predicate;
executed results belong to the task evidence, while full installed replacement,
exhaustive publication boundaries and TLC remain separate obligations.

The R02.g canonical-entry rows now interrupt staged-file/parent sync, rename,
post-rename return, final-parent sync and post-readback return. Recovery of an
already published record maps to the existing SyncReceipt and ReadFinal actions:
the explicit entry holds its original final inode through file/parent fsync and
fresh retired-fact/name readback, without rewriting or replaying earlier effects.
Read-only detection performs no sync. The lower keeper observes sync refusal and
replacement/reappearance during confirmation; the composed rows retain their
actual produced-receipt R03/R04 checks. No model state/action/configuration or
TLC result is added by this concrete boundary mapping.

The retained B6 custody venue now seeds synthetic historical native, account,
publication and activation inputs from `scripts/home_legacy_fixture.py` and
frozen legacy config/DERP/policy bytes. It no longer prepares a new home
installation or imports the activation composition to exercise real cleanup.
The activation-only disable/re-enable scenario retired with its backend owner;
retained diagnostic/no-follow cleanup and namespace cases keep the actual B6
boundary. This fixture separation adds no model transition.

Ordinary home install and supervisor start/restart/enable/foreground now refuse
valid legacy state with the accepted retirement message before native effects.
The retained HomeProfileAdmission adapter permits only explicit read-only
legacy inventory. This implements OldAdmission refusal without a new model
action, account-positive path or destructive startup transition. Other Python
activation/provisioning owners remain pending their separate source retirement.

Legacy inventory/unit readback now uses the fixed `legacy_home_readback` reader,
original transaction directory lock, token check and pure historical config
rendering without importing activation/admin/minter owners. Existing writers
temporarily inherit/re-export those unchanged implementations. This dependency
separation adds no model action, proof predicate or account admission.

The retained legacy admission/inventory cases now seed independent historical
config, receipt/transaction and frozen native inputs through the neutral fixture.
They construct only retained readers and native observation adapters, rather
than activation/admin/minter owners or a live-config publication transition.
Their production inventory and refusal assertions remain unchanged; this test
setup separation changes no model transition or execution-evidence claim.

The helper's fixed generation child now selects the retained legacy readback
module. Its root-only zero-option entry emits the same closed metadata snapshot
without a writer import. This command movement adds no authority or model action;
helper authentication and original transport budgets remain separate obligations.

Public helper Enable/ReplaceFailed now refuse after the original sender/owner,
native authorization, budget and revalidation checks and before any worker or
effect. The root child retains only inert capture/refusal adapters; the positive
activation backend/composition is removed. This strengthens OldAdmission refusal
without adding a model transition. Disable still uses the same protected
installer directory flock. Invite/lower writer removal remains separate; TLC
and installed native acceptance are NOT RUN.
