# Sequential Coding U9: release selection and atomic fresh-start cutover

> Status: current design; U9a interfaces delivered; TASK-15616 delivers only U9b obligation 1 (inert offline registration/separate admission) in the expressly permitted first PR. Atomic obligations 2+3 and their parity/evidence remain implementation. Public fresh start stays OFF; old start@2 admission is unchanged.
> Current Source: docs/agent-guides/contracts/reusable-workflows.md; docs/runbooks/sequential-coding-cutover.md

THR-336 / ROOT TASK-15606. U9a maker TASK-15613; inert U9b setup maker
TASK-15616 at base `9c481ce589ff3cdba65cfe22288eebaa309fb2df`. U9a base
`fb1ab588d48ac5d9e7cfc25e7025dc2abaa3cbed` includes U8b PR1442. Plan rev3
TASK-14461 §U9 / AC1/AC2/AC3/AC5/AC12 and R-M2-1 ("U9 unchanged") govern.
PRD v1.0.4 Shared Artifact
`product_lead-2026-11-11-workflow-mechanism-prd-v1.0.4.md`, SHA256
`96925395de4e44cc5c1570deb512ec843fcfd72c0b3eaeff0423c00b58a07bb5`, and
Founder THR-336 msg131 ACCESS-1 control. Source delivery is not installation,
deployment, admission, loaded identity or genuine execution acceptance.

## Split declared before edits

The U9a brief permitted a first coherent interface unit. At that checkpoint the only
definition/settings publisher, `setup_sequential_venue.py`, excludes the live
home and requires disposable stopped-state/marker custody. It cannot serve as a
public operator path. U9a supplies the compatible source selector and refusal
interface, with **no production importer**, before implementing that path and
the paired cutover. This is not full U9 delivery or an activated old-start fence.

Both mounted admission paths remain byte-identical: `check_venue_start` stays
at sequential `precheck_start` (intake and committed-result consumption), and
old Coding `require_start_ready` / inner writer retain their prior behavior.
There is no interval exposing public new fresh start ahead of the old-start@2
fence. **U9b must attach both together in one merge unit**, without an enable
flag or historical fallback. No schema, endpoint, credential, permission,
dependency, startup-order or live-state change is part of U9a.

## Inert U9b setup split (TASK-15616)

Declared in `dev_agent/output/TASK-15616/plan.md` BEFORE source edits: use the
explicitly permitted obligation-1 first PR. `scripts/setup_sequential_coding.py`
is a separate non-venue cooperative offline operator entry point. It reuses the
existing schema installer’s org resolver, checkpoint/file and lifecycle
observations; TASK-15618 supplies operator-scoped preservation/verified backup.
It never imports/reuses the disposable publisher or
marker. Installation remains `install_workflow_sequential_schema.py`, unchanged.

`register` reads ONLY `read_coding_release`’s reviewed Coding v2 resource, compares
all stored bytes/source/ABI fields, and appends only a missing definition. It
creates no settings/admission/tasks. Exact registration is a read-only no-op.
`admission` accepts the closed canonical <=16384-byte
`workflow-sequential-coding-admission@1`: exactly format, operation_key, full
definition pin, expected_generation (positive INTEGER), and Allowed/Disabled
admission. It requires existing NEW defaults and copies only their bindings and
limits under generation CAS. It never reads old Explicit settings. Initial Save
stays Disabled; later Save preserves the separately published admission.

The offline settings operation principal `operator/sequential-coding` keeps
admission operations separate from existing Founder PUT/original-save lookup.
This is immutable operation provenance, not an executable principal, auth rule
or permission. A saved key selects and authenticates its original command and
receipt before current-generation CAS; exact replay writes nothing, changed
body refuses. `admission-show` is read-only and distinguishes original receipt
from latest generation/admission. Absence/error grants no resend or new key.

Every invocation refuses the existing agent-environment markers and requires
operator-confirmed complete cooperative offline/native-owner/job exclusion plus
registered stopped-state observations. Fresh writes require a separate verified
checkpointed backup. Complete source/target readers, exact scoped preservation,
identity/lifecycle/backup rereads and one 900-second cooperative deadline surround
one BEGIN IMMEDIATE/COMMIT. No install/repair/startup/OrgState or daemon/network
operation occurs. COMMIT attempt, rollback/close/delivery or post-publication
uncertainty returns exit4 and preserves the original command/key. No retry.

Both public and old fresh-start paths remain byte-identical in this PR.
`check_venue_start` still gates sequential intake and consumption, even when new
settings are Allowed. `select_coding_release` and `refuse_legacy_fresh_start`
still have NO production callers. The only new caller of `read_coding_release`
is this explicit offline operator. No marker is created/removed, and V3 fixture
behavior is unchanged. Obligations 2+3 MUST follow in ONE merge unit; this split
does not complete U9b/U9 or remove the pending-U9 host setup message.

## Retained-history repair (TASK-15618)

R1 removes this operator's unconditional whole-database Python inventory. It
snapshots complete `sqlite_schema` and exact typed rowid/cells of every table it
can write: `workflow_seq_definitions`, `workflow_seq_settings`,
`workflow_seq_settings_operations`. Source, verified backup and locked source
must match. A native SQLite authorizer permits only the action's literal INSERTs
and refuses other tables, UPDATE/DELETE, DDL and trigger-originated actions.
Actual change count and exact expected appended rows, original rows and SQL are
compared before COMMIT and again after it. Full original family readers and
preflight whole-database FK checks remain; invalid history still refuses.

Full backup SHA256 is streamed with stable file/checkpoint identity bookends;
backup equivalence now compares all writable rows/schema, not unrelated ordinary
historical payloads. The supplied digest still authenticates the entire backup.
This is a cooperative offline contract, not racing-writer/OS exclusion. No
transaction, original-key receipt, CAS, publication/close/rollback/delivery
uncertainty, agent-environment refusal or fail-closed validation gate is removed.
Ordinary history growth cannot consume the scoped copy allowance. Read-only
original-key replay/readback never hash or require a backup. Disable remains
reachable across the sized history envelope; invalid or exhausted workflow-family
history still refuses rather than returning partial validation.

Sizing lower bound observed by TASK-15617:1,452,916,736 main-file bytes,
15,617 tasks,27,327 task_results, excluding live WAL. Tenfold target is
14,529,167,360 bytes,156,170 tasks,273,270 results. Synthetic verification targets
at least 160,000 tasks and 273,270 results with >=14,529,167,360 actual file bytes;
source diagnostics report encoded payload bytes separately from file allocation.

| Operator cap | Explicit production arithmetic |
| --- | --- |
| One 900s deadline, all phases | Target 14.529GB /32MiB/s =433.0s backup hash, leaving 467s for complete-reader/SQL/I/O/bookends; tenfold old 5s is 50s. Cooperative checks do not bound a blocking OS call. |
| Full backup 32GiB | 34,359,738,368 bytes =23.65x observed,2.365x tenfold target. At full cap 900s requires 36.41MiB/s absent other work; tenfold target requires 15.40MiB/s. Exhaustion refuses before writes. |
| Hash 4MiB chunks | Tenfold target 3465 blocks, maximum 8192 blocks; deadline checked each block, fixed buffer size. |
| Ownership/FK guard 640M SQL steps per complete read | Operator-only; default 32M unchanged. 160k valid legacy-owned tasks measured 40.983M ownership steps (~256/task), leaving 15.62x headroom; 640M is 20x original 32M. No predicate is omitted. |
| Scoped preservation 8192 acquired rows,64MiB raw/copy,1MiB cells,65536 work | Same complete-reader ceilings, including schema/copy overhead. Tenfold ordinary156170+273270 history contributes ZERO acquired preservation rows/payload bytes; only three writable families enter this copy. |
| Family/schema 2M SQL; retained legacy original limits | Unchanged. Ordinary ASCII legacy-owned history uses SQL-only guards, not acquired workflow rows. Workflow/candidate exhaustion still refuses. |
| Canonical command 16384 bytes; busy timeout 1s | Closed ~700-byte five-field command has >23x headroom, independent of history; lock admission remains fail-closed. |

Caller disposition: `setup_sequential_coding.operation` and its backup/writer
checks now use script-local `_readonly_inventory`, `_backup_observation`,
`_inventory`, `_preserved` and `_insert_only`; `_inspect` uses `_history`.
`_history` is the sole non-default caller of `read_sequential_history`'s internal
`ownership_sql_limit` parameter. Runtime/installer callers keep 32M and every
existing data allowance. `sequential_storage_inventory` still has exactly the
installer `_readonly_inventory` caller (also its backup path) and
`install_sequential_schema` pre/post writer callers.

**Known pre-existing installer defect, deliberately deferred:**
`install_workflow_sequential_schema.py` and `workflow_schema.py` are byte-identical.
Their whole-database8192-row/64MiB/5s inventory, plus installer8192-owner copy cap,
already blocks normal retained production history. This operator repair grants
no claim initial installation works there. Follow-on installer sizing and
preservation evidence/doc parity are required; no live workaround is authorized.
The optional reader allowance preserves the installer's default behavior.

N1: TASK-15616's original cases labeled missing-marker actually observed
`sequential_venue_live_home`. Corrected outside-live-home disposable diagnostics
assert exact `sequential_venue_marker_absent` before SQL at old/new heads. These
are synthetic observations, not genuine V3 acceptance. TASK-15617 remains
REQUEST_CHANGES history; prior failed/blocked/REQUEST_CHANGES roots and spent
allowances remain unchanged.

## U9a interfaces and caller inventory

`sequential_release.read_coding_release` reads only the constant bundled
`coding.v2.json` via the package loader. Acquisition reads at most 3796 bytes
and requires exactly 3795, source SHA256
`5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065` and its
single presentation LF. Remove exactly that LF, then use the unchanged common
compiler. Require identity `release/happyranch/coding`, version 2, canonical
SHA256 `a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c`.
The result carries the complete original compiler/validator/interpreter/
capability ABI@1 pin. Unsupported loader/missing bytes returns
`sequential_release_unavailable`; changed/oversized/invalid bytes returns
`sequential_release_corrupt`. There is no filesystem or v1 fallback, storage
write, automatic registration or availability cache.

`select_coding_release` re-reads that resource and compares the ENTIRE supplied
`DefinitionPin`, including source and ABI pins; mismatch returns
`sequential_release_pin_mismatch`. It is a fresh-only selector, never a
historical reader or interpreter branch. Different definition data still uses
the common engine; this selector does not hard-code task progression.
The frozen U1 v2 resource contains historical four-role preflight wording;
current trusted U4 supplements and ACCESS-1 supersede that wording. Do not
rewrite its bytes/pins or restore the removed checkpoint.

`refuse_legacy_fresh_start` unconditionally raises
`coding_fresh_start_retired`, with no caller enable Boolean or readiness input.
Its future caller must first select and authenticate admitted replay. It owns
no old-family parsing, callback, settlement, cancellation or LOCAL action.

| Existing owner | Current caller / U9b requirement |
| --- | --- |
| `CodingStart`, `CodingSettingsStart`, `parse_start` / `parse_decision` | `NextStep`, completion validator and local `workflows coding run-start` parser. Preserve literal @1/@2 syntax/history; local composition alone never admits. |
| `tasks.submit_completion` → `require_start_ready` | Legacy fresh callback branch before `admit_task_completion_callback`. Fence @2 here before native callback writes; retain earlier exact historical replay branches and 404 → ownership409 ordering. Literal @1 already fails fresh settings-family admission. |
| `run_step` → `CodingStartStore.consume` → `_start` | Before ordinary v2 action spending. Future fresh refusal must not strand an exact already admitted original command/quartet. |
| `CodingStartStore.start_in_writer` | Sole production caller `_start`; complete inventory and exact source/result/task/SID/key/body replay precede its fresh branch. Put the inner fresh fence after that authentication and before native/capacity/action/graph writes. |
| `precheck_completion` → `precheck_start`, `consume_start` → `_consume` → sequential `start_in_writer` | Same genuine ordinary-manager/native ownership and atomic graph writer. Attach selector/public boundary coherently at intake and fresh inner admission, before writes/spend/allocation; retain original first-visit positive host/outer gate. |
| `OrgState` / historical builtin registration | Preserve normal inert Coding initialization and profile/startup/native ownership ordering (PR1262). No sequential install or config conversion on startup. |
| U7a/U7b/U7c, engine/history/dispatch, U6, old Coding families | No edits in U9a. Future attachment does not select current settings for admitted replay, mutate stored pins or enter legacy Coding progression. |

## Exact compatibility table

| Family / state | U9a + inert U9b setup | Required after atomic U9b |
| --- | --- | --- |
| Literal `workflow-coding-start@1` | Historical syntax/readers; not a fresh-start route | Same historical behavior; fresh refusal before writes/spend/allocation |
| Old `workflow-coding-start@2`, fresh | Existing Explicit/settings/native gates unchanged | Categorical fresh refusal, even with old Explicit settings and healthy old engine; zero legacy fallback |
| Old @1/@2, admitted | Original pins, exact replay, callbacks, settlement, cancellation and LOCAL Final | Same original owners; later old/new settings edits never re-pin or strand work |
| `workflow-sequential-start@1`, Coding v2 | Disposable venue only; Allowed/current pins/native checks still required | Only reviewed v2/common engine for fresh public Coding, under current new settings and native runtime; unavailable new storage/engine visibly refuses |
| Sequential admitted run | Original fixed definition/settings/bindings/limits, U6/U7 settlement/cancellation/return | Same historical pins, including after defaults/disable edits; no fresh-only selector on continuation |
| Draft → Review / data-variation fixtures | Existing disposable venue path, outside production catalog | Preserve explicit V3 fixture path and common-engine proof, never a public Coding fallback |
| Old settings / uncertain save | Original repo-bearing bytes, key and receipt reader; pointer-only Coding page | Never translate, re-key, resend or transfer Explicit admission; original read-only recovery retained |
| New settings | Repo-free defaults; first save Disabled, later save preserves admission, zero work | Same; offline operator Allow/Disable source is delivered, separate from Save and requiring separate operating authority |
| Missing new storage/definition/engine | Public OFF; explicit read/refusal state | Fresh refusal, zero old progression; availability/defaults/admission/loaded identity/execution acceptance remain distinct |
| Ordinary delegate/then/fanout / legacy routing | Unchanged | Unchanged; no missing-ownership fallback |
| Empty installed sequential extension | Compatible reader still required | No old-code-only downgrade, table drop, pin/mark rewrite or effect-undo claim |

## U9b implementation remainder and atomic obligations

1. **SOURCE DELIVERED in the inert first PR (TASK-15616):** explicit reviewed
   non-venue definition registration and separate
   admission action under existing operator/settings ownership. Keep defaults
   Disabled and preserve admission on Save. Do not reuse/relax the disposable
   publisher as a live bypass, install at startup, convert old Explicit settings
   or add a top-level enable flag. Installation remains the existing explicit
   reviewed offline schema path, with original backup/native exclusions.
2. **REMAINS IMPLEMENTATION, paired with obligation 3:** in the SAME merge unit
   mount the v2-only selector/public fresh-start boundary
   and old fresh-start@2 fence at ALL intake/consumption/inner-writer seams above.
   Complete admitted replay must precede fresh fencing. Check mounted native
   engine/storage/current authority and latest Allowed settings independently;
   no callback/spend/graph writes on fresh refusal. No new start endpoint.
3. Replace `check_venue_start` only for public Coding under that combined cutover;
   explicitly retain the disposable fence for V3 noncatalog fixtures. A missing
   marker must not accidentally become authority, and a marker must not bypass
   fresh Coding release selection. U9a makes neither change.
4. Renew doc/skill/setup-copy parity for each unit. TASK-15616 supplies setup-split
   parity; atomic parity/evidence remains, including removal of the U9-pending
   operator message only when true. Supply full B/H/caller/negative-space inventory, exact-head
   static job/hosted receipts, independent review and scoped QA. Exercise genuine
   bounded V3 compatibility/install/reopen/backout and old/new/mixed-history
   cases only under remaining original authority/window; record missing proof.

No public attachment is complete until all four obligations land. Any unresolved
ownership/product contract, credential/permission, non-additive schema or daemon
GitHub/network need returns to the manager before dependent edits.

## Operator repair and evidence limits

`setup_coding.Coordinator.serving` and `run` now point directly to
`/orgs/ORG/workflows/setup`. Human confirmation still requires actual page
rendering without a placeholder/error; catalog availability is separate. Handoff copy describes
repo-free defaults, separate sequential registration/install/admission and
non-transfer of historical Explicit settings; it no longer offers removed
repository/format controls or calls v2 automatically available. No setup effect
or historical observation predicate changes. The two U8b conflict summaries now
match operation_conflict >= refused basis versus generation_stale > refused
basis, both at/above the committed floor; frontend implementation is unchanged.

Out-of-repo diagnostics are SYNTHETIC source/resource and extracted-copy checks,
not native/V3/V5/M4, operator execution, persistence, access, genuine merge or
installation acceptance. No repository test added/changed. Python units/Vitest
RETIRED, integration SKIPPED THR243seq42, Product E2E untriggered. Exact B/H,
full inventory, commands/exits, authenticated clean-H local-CI job and hosted
snapshot are in `dev_agent/output/TASK-15613/handoff.md`.

Preserve U7a GETs, U7b CAS/lookup, U7c Final/witness/R-M3-6 redaction, U7d intake,
U8a/U8b custody, all old Coding families/readers, TASK-15250 original Pause
generation, TASK-15272/15275 command=None/no reissue, U6b2a, Option B ordinary/
document UNSETTLED, R-M3-2 allowances and U4 clone preflight. All previous failed
tasks/RCs and spent allowances remain. F-1=(i), Decision1=B (48 KiB), Decision2=1,
SB-1=(a), ACCESS-1, held PR1252 and V3 end 2026-11-23T08:48:38Z remain.
Live install/migration/config/admission/roles/deploy/restart, genuine scratch,
effect recovery and window extension remain Founder-reserved. No notifications,
force-push, hook bypass or maker merge. Merged != deployed.
