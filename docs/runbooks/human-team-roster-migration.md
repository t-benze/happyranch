# THR296 bounded roster migration

This candidate is not a maintenance release. No successful check, production
manifest, backup/restore receipt, M venue, apply or deployment is attested.
Independent code review, behavioral QA, exact-head checks and explicit manager/
operator maintenance authorization remain required. Do not run against this host.

The utility changes only the two consultant definitions and active teams.yaml,
with declared normal generated refresh and native reset/publication/profile
owners. Names, workspace/provider paths, memory, history, retained policies and
thread delivery/breaker state stay in place. No SQLite schema is added.

Use an actual authorized disposable M venue before maintenance readiness:

1. Stop and persistently inhibit every registered supervisor/alternate launch
   path. A workflow fence or PID file alone is insufficient. Record actual unit
   inventories, persistent masks, normal-start refusal and reboot persistence.
   Retain inhibition through failure; release is a separate authorized action.
2. Complete quiescence and inventory without capped scans. Close/checkpoint the
   org database. Create owner-only external durable backup storage, preserve
   bytes/type/mode/owner/raw links, and verify an isolated restore with full
   retained history and FK/integrity checks. Do not publish private memory.
3. Prepare real check input, rather than TASK10389's design JSON: operation_id,
   private durable operation_dir outside runtime/workspaces/tmp, exact `reader_binding`
   (resolved source root/clean HEAD, Python executable/hash/version), exact closed
   database backup inside an independently restored complete closed org copy
   (`closed_restore_root`), sole existing head manager sentence, actual persistent
   systemd user/system-unit and registry paths bound to the effective `daemon_home`, and independently inspected exact
   generated after-images from unchanged candidate materializers. Every image
   names kind, mode, uid/gid, base64 bytes and SHA256; links retain raw targets.
   Include `closed_canonical_store_restore` for the effective original shared
   store, `canonical_store_after_images` for every new native package file and
   containing directory, and `materialization_event_shapes` for the existing
   catalog's normal info events for both agents. Existing package addresses
   cannot be rewritten. Directory images contain only kind/mode/uid/gid.
   Check captures old publication/profile row hashes and audit/materialization
   prefixes; initial apply rechecks exact closed DB bytes and all those controls.
   Recovery refuses foreign journals, profile operations or new audit actors.
   It checks native namespace/invocation/snapshot and leftover lease ownership
   before recovering a reserved publication phase through its existing owner.
   Live lease owners and foreign profile leases refuse. Runtime marker and
   registry images remain exact; schedule quiescence includes firing/session
   ownership even when a schedule has been deactivated. Native refresh previews
   must cover the existing six-context skill union and integrity validator for
   both workers, plus the actual file/directory durability boundary.
   Existing executor dependency rows remain exact. Resolve unfinished journals,
   profile operations, stale leases and incoherent bindings through their existing
   owners before checking. Native provider directories and relative skill links
   must be declared; arbitrary provider files are outside this utility's radius.
   Unknown generated paths or missing capabilities refuse.
4. On a clean committed candidate, invoke:

   ```sh
   uv run python scripts/migrate_human_team_roster.py --check --runtime-root R --org O --plan /durable/OP/check-input.json
   ```

   Retain stdout as a private checked manifest only on actual exit0. Record its
   exact SHA256 and current source/interpreter provenance. Independently inspect
   all inputs, canonical delta and generated closure before apply authorization.
5. After exact manifest authorization, apply is:

   ```sh
   uv run python scripts/migrate_human_team_roster.py --apply --runtime-root R --org O --manifest /durable/OP/manifest.json --expected-digest D
   ```

   Recovery is state-based with identical manifest/digest/operation:

   ```sh
   uv run python scripts/migrate_human_team_roster.py --recover --runtime-root R --org O --manifest /durable/OP/manifest.json --expected-digest D --operation-id OP --direction complete
   ```

   Substitute compensate only before traffic and with explicit authorization.
   Never restore an old authority pointer or provider resume ID. Unknown third
   states preserve inhibition and require a newly inspected forward repair.
6. Verify complete roster/roles, both native reset/audit facts, exact generated
   closure, profile readiness and actual ready generation/journal/digest. A false
   publication helper or log is not readiness. Use source-bound and filesystem
   observers for crash/replay/no-helper/no-write proof; equality alone is not it.

Unproved candidate requirements remain explicit: receipt-loss reconstruction
without another generation/reset/materializer invocation; complete
supervisor and generated/global materializer closure; closed backup/restore
metadata and preservation inventories; RF5/RF6/writer-busy causal process controls;
M reboot/maintenance and zero-write replay; held old-reader and U9 localization;
bilingual viewport screenshots. This draft utility must not be accepted as
canonical C6/C8/C9 completion while any of those source/proof gaps remains.

Reviewable check input uses this shape. The placeholders are required real
observations and exact native outputs, **not an executable manifest or receipt**:

```json
{
  "operation_id": "OPERATOR_ASSIGNED_UNIQUE_ID",
  "operation_dir": "/durable/private-operation",
  "reader_binding": {"source_root": "MEASURED_ABSOLUTE_CANDIDATE_ROOT", "source_sha": "MEASURED_CLEAN_HEAD", "python": "MEASURED_RESOLVED_PYTHON314", "python_sha256": "MEASURED_EXECUTABLE_SHA256", "python_version": "MEASURED_SYS_VERSION"},
  "closed_restore_root": "/durable/verified-closed-org-copy",
  "closed_database_backup": "/durable/verified-closed-org-copy/happyranch.db",
  "closed_canonical_store_restore": "/durable/verified-closed-store-copy",
  "head_manager_sentence": "EXACT_SOLE_EXISTING_SENTENCE",
  "containment": {
    "daemon_home": "/effective/original/daemon-home",
    "systemd_user_units": ["ACTUAL_REGISTERED_SUPERVISOR.service"],
    "systemd_system_units": [],
    "registry_paths": ["/effective/original/daemon-home/runtimes.yaml"]
  },
  "generated_after_images": {"EXACT_NATIVE_WORKSPACE_PATH": "REPLACE_WITH_CLOSED_IMAGE"},
  "canonical_store_after_images": {"EXACT_NATIVE_PACKAGE_PATH": "REPLACE_WITH_CLOSED_IMAGE"},
  "materialization_event_shapes": ["REPLACE_WITH_EXACT_NATIVE_EVENT_FIELDS_EXCEPT_ID_AND_CREATED_AT"]
}
```

Check is read-only with respect to runtime state. Do not redirect its stdout to
a production manifest until a real authorized check returns exit0 and its full
private contents have been reviewed. No M capability or backup receipt is
created by this example. Successful completed replay checks the owned final
state before first-apply CAS; loss of an external receipt permits only its
reconstruction from actual native evidence. Partial prefixes require explicit
recover. Only the exact declared native package staging bytes and metadata are
recognized as an owned prefix. Unknown global residue is refused, never swept
or adopted. Any changed global prefix also requires this OP's authentic native
publication journal; matching package names/bytes alone confer no ownership.
Native creation-mask provenance is rechecked before materialization.

The source-bound admission probe is now authored at
`tests/helpers/human_team_incompatible_reader_probe.py`. Actual old-source
execution remains HELD. The concrete negative uses the accepted b317 checkout,
an independently prepared closed **agent-managed** schema2 control with a genuine
ready journal/pointer, and its independently recorded snapshot digest:

```sh
HAPPYRANCH_DAEMON_HOME=/venue/reader-home /venue/old-b317/.venv/bin/python -I /venue/candidate/tests/helpers/human_team_incompatible_reader_probe.py --source /venue/old-b317 --source-sha b3179b123fddbb0f0f604ed9e0d148f1b23455f3 --root /venue/reader-runtime/orgs/reader-case --org reader-case --operation capture-admission --expect workflow_activation_authority_stale --snapshot-digest ACTUAL_SCHEMA2_DIGEST
```

Bind an isolated mode-0700 reader daemon home and Python3.14. The helper uses
actual reader constructors, unfenced public profile binding and native recovery
before measurement; it verifies module origins and input digest. Measured capture
does not republish schema1, replace a validator or manufacture a graph/receipt.
Positive controls use `--positive-graph` for separate selected-source route
admission after that measurement; the negative never enters those writers. Its exact
row/file readback assertion alone does not prove zero syscalls. Candidate/schema2
and b317/schema1 positive graph/admission controls, the external observers,
causal control/restoration and five specified repetitions remain required under
the manager's concrete held-source execution disposition.

Native interruption closure is source-correlated, never filename-only. A new
OP-owned publication journal must exist before recovery can recognize new
instruction siblings. The native timestamp/collision suffix must match the
unchanged writer; regular staging bytes must be a prefix of the checked
original/target bytes with the native owner/mode, and staged links must have
the exact checked raw target. Pre-check siblings remain preserved and are
never adopted. Explicit recovery closes only those validated temporary paths;
complete retains verified original-byte preservation copies in the actual
receipt, while compensate removes only newly OP-correlated copies. Unlisted
siblings and unknown bytes/types/metadata refuse. Live roster/instruction files
remain atomic before/after images. Only declared settings/opencode outputs
written in place by the unchanged adapters admit an interrupted target prefix.
Shared-package staging admits only declared native directories/files and exact
target-byte prefixes; unknown members still refuse. Readiness requires complete
generated outputs, fully verified preservation copies and zero temporary
residue. Missing containing directories must be declared with exact metadata.
These are authored recovery rules, not successful crash/maintenance evidence.

Authority staging is tied to the authentic OP-owned journal's exact snapshot,
phase, bytes and creation metadata. Explicit recovery discards only a checked
partial temporary file before invoking the unchanged native coordinator. A
complete reserved stage remains for that coordinator to publish; a discarded
partial reserved stage lets it record its genuine unpublished abort unless
the canonical snapshot already landed. Forward recovery rebuilds its missing
canonical snapshot through the native owner. The utility never writes a
journal/pointer to manufacture readiness, and unknown staging refuses.

The systemd inventory includes pre/start/post/reload commands. This bounded
adapter refuses every undeclared command-bearing unit: executable bytes/hashes
and a plan assertion cannot prove its configured/internal launches exclude
the runtime. A venue with ordinary unmasked system services therefore has an
explicit missing containment capability, not a successful check. Resolve the
exact venue/unit closure with the manager before maintenance authorization;
the utility does not mask or stop services. Desktop autostart or executable
rc.local is outside this adapter and refuses. The same-user census also includes
daemon scripts, direct daemon-module paths and executor binaries. This remains
a cooperative, explicitly inhibited disposable-VM operation: no host exactly
once or hostile same-user exclusion claim follows. Actual M capability, reboot
and independent complete launch-inventory evidence remain required.

C7's ten L-only context parameters remain in
`test_c7_both_resume_resets_and_worker_contexts` (head/codex ×
task/thread/dream/wake/schedule). Their closed test-side helper and registered
provider stubs capture exact stdin (including final newlines), binary/argv,
source/helper/plan hashes, actual workspace and runtime binding, complete
AGENTS/CLAUDE/settings and both skill-root member manifests. Literal ordinary
worker/advisory, self-only task decisions, supported sandbox/settings and
six-contract no-repository union are checked independently of the captured
values; native permission/eligibility algorithms remain unchanged. Generic
conditional manager guidance is not itself an active manager grant.

Two archived cooldown control threads have nonempty frozen exchange and breaker
receipts; the eligible thread has retained ACK7 plus an earliest transcript
marker. Each native per-agent reset is independently read with its own audit,
with no claim of an atomic transaction across both agents. Reset creates no
message, invocation or notification and leaves the third agent, watermarks,
cooldown, frozen history and original runtime/provider memory paths/bytes
unchanged. Later launches have separate legitimate-effect assertions.

The task parameter uses genuine self-child continuation: the original admitted
root session delegates to itself, the real child settles, and the same logical
root is admitted with a new session. The original captured tuple is submitted
through the unchanged CLI and must return its precise session-mismatch refusal
without a result, recovery-consumption, audit or task mutation; only the current
callback then succeeds. No current_session_id, runtime session or result is
seeded. Thread consumes its exact real token with one owned reply. Dream has its
own completed row/transcript, empty learnings/candidates and no founder thread;
wake/schedule have their own transcript and exactly one genuine own-worker
root/result, with one-shot schedule inactive, fired once and session cleared.
Provider exit status and independent post-exit runner usage are both required,
with bounded observation that fails at its deadline. Callback exit0 alone is
not runner settlement. Missing/duplicate/conflicting outer IDs refuse; skill
examples and transcript history cannot supply callback identity.

These are AUTHORED assertions, not executed proof. L already-human seed/reset
is explicitly not migration. An after-M claim requires the real C6 utility
--check → exact-digest apply → compatible readback → genuine operation receipt
in the same supported disposable M fixture before release and launches. That
combined venue remains unprovisioned, including persistent inhibition and
observer/reboot capabilities; no successful M receipt or demotion is inferred.
Python collection/helper/case/causal execution and five-isolated/sibling
repetitions remain SUSPENDED/HELD. The task evidence's existing C7 source map
records producers, native readers, final oracles and inert restoration controls.
Source authoring alone does not close C7 behavioral acceptance. Wbrowser's
finite selection is now authored at
`tests/integration/test_human_team_roster_browser.py`, using the built SPA and
the fixture-owned loopback daemon with explicit loading/error/empty transport
states. Browser capability, execution, screenshots and independent bilingual
assessment remain unavailable/unobserved, not PASS.

The accepted test-side frame/syscall helper files are authored, not executed or
capability-approved. Frame input is a closed JSON object with `source_sha` and
`sites`; each site has relative `file`, whole-file `sha256`, exact compiled
`qualname`, and `events: ["call", "return"]`. Both native reset entries in
`runtime/infrastructure/db/sessions.py` (`SessionsMixin.reset_thread_sessions_for_agent`
and `SessionsMixin._reset_thread_sessions_for_agent_uncommitted`) are mandatory.
The helper records actual calls/returns against independently compiled code.
It does not locate interior SQL commits or prove descendant coverage alone.

The paired Linux x86_64 syscall helper follows its own child threads/descendants,
records actual entry/exit/path/fd/inode facts, and refuses unsupported descriptor
transfers, shared mappings, unresolved writers or incomplete tracking. Its
currently authored finite cuts cover manifest-indexed canonical/generated
stage-write/file-flush/rename/directory-flush paths. Authority/receipt-specific
cuts and pass-through real-transaction localization are authored in the finite
source map below. Capability/loss/kill controls remain unexecuted; an unsupported
cut or observation is UNAVAILABLE, never PASS.
No helper was run on this host. Zero-row/already-null replay and M reboot proof
remain outstanding even when final file hashes or audit counts agree.


C1 bootstrap and C5 source closure (TASK10458): an exact unique pending bootstrap
manager attaches without becoming executable; ordinary approval/init must publish
the active manager before owner/workflow admission. HTTP removal of a still-active
declared worker is invalid and rolls back. Native registry roundtrips are distinct.

C5 source cases now author actual activation/draft/graph/receipt producers, fixed
independent schema1 historical admission bytes/IDs/digests, schema2 human/agent
publication, stale draft and malformed/unknown-version refusals, explicit/no-profile
closure, and compatible cold reopen. Template-definition schema1 is unchanged.
The positive reader controls separately admit real graphs after measured read-only
capture; the b317 schema2 negative stops at capture_admission before any writer.
Historical fixture data is never presented as an executed old callback/result.

The compatible-restore selections author a closed file/DB copy, independent exact
backup/restore metadata and digest comparison, supported RuntimeDir registration
into a second owned root, and coherent current/historical readback. Explicit
profiles retain their actual owned same-machine dependency paths; no path rewriting
or export/import API is invented. HAPPYRANCH_TEST_ROSTER_M_VENUE selects a root
only and grants no authorization/capability. M remains unprovisioned/HELD. These
assertions have not executed and provide no successful utility/restore receipt.
TASK10461 extends C6/C8/C9 source below; the root still owns C7 execution and
combined-M proof, C10 completion,
actual old-reader/M/L/browser receipts, causal controls,
five isolated/affected-sibling repetitions, independent final-head review/QA,
publication/CI and operator acceptance. Python units/collection/control/repetition
remain SUSPENDED THR291seq5/16; general integration remains SKIPPED THR243seq42.


C6/C8/C9 maintenance source (TASK10461) is AUTHORED / UNEXECUTED. This is
not a successful M setup, observed migration or maintenance-readiness certificate.
The shared `_MaintenanceCase` invokes the real utility check, stores its actual
stdout manifest and digest, then invokes exact-manifest apply/recovery in new
processes. Native preview is fixture setup, never a checked manifest or final
roster oracle. Exact file/row/history comparisons and compatible cold readback
use the actual operated root. Fixture history is labelled synthetic DATA.

| Existing owner | Final finite selection | Proof status |
| --- | --- | --- |
| `test_c6_exact_manifest_apply_and_preservation` | no Default / correct empty human Default (2) | AUTHORED; M unavailable |
| `test_c8_preflight_refusals_and_backup_cas` | 3 retained early controls + 50 single-condition M refusals | AUTHORED; each M refusal requires its real positive check first |
| `test_c9_crash_recovery_and_replay` | 4 retained early controls + 279 M selections: 160 canonical/authority/receipt syscall cuts, 96 actual frame cuts, 4 finite checked generated/global path selections, 4 backup durability cuts, 8 shared-store directory-flush cuts, 3 observer loss/capability controls, 4 third states | AUTHORED; process SIGKILL, never guest reboot |
| `test_c9_reset_atomic_boundary` | original 12 reset parameters unchanged | SUSPENDED THR291 |
| `test_c9_publication_commit_boundary` | 7 actual journal/profile phases × before/after real COMMIT × complete/compensate (28) | SUSPENDED THR291; shared fixture also needs M |
| `test_c9_replay_no_helpers` | ordinary / zero rows / initially NULL and zero (3), two actual replays each | SUSPENDED THR291; mandatory complete paired observers |

Generated `gN` and global `sN` cuts come only from sorted actual manifest paths,
types and SHA256, saved in `checked-syscall-paths.json`; no unbounded discovery
campaign is implied. Existing package refresh and shared-store directory flush are selected. Native
new-package staging aliases/copy writes/package rename are mapped only when
the actual manifest declares a new address; that positive selection still needs
an independent closed-store preview and is not exercised by this fixture. Before-syscall cuts record an entry and explicit nonexecution;
there is no invented return/exit. After cuts include the actual return and
inode/path evidence. WAL flushes are never SQL COMMIT evidence. Immediate
second-reader SQL prefix and normal exception unwind are distinguished.

Replay verifies complete observer receipts before asserting no helpers/writes.
Loss after durable completion reconstructs only the external receipt from real
operation-owned rows/audits/journal/pointer. A successful `receipt.json` remains
unchanged when explicit compensation publishes a NEW coherent original-roster
generation; `compensation-receipt.json` records that later direction. Both
resume resets and truthful audits remain. A compensated operation requires a
fresh checked manifest for another apply. Unknown bytes/writers/history or an
incomplete backup refuse without overwriting them.

The external M input is a location/inventory declaration only:
`HAPPYRANCH_TEST_ROSTER_M_INPUT=/durable/private-M-input.json`, with
`kind: THR296-test-fixture-input-not-manifest`, a real owner-only `venue` outside
`/tmp`, and `containment` matching the check-input example. It grants no
permission or inhibition. Do not execute these commands while held; after the
manager authorizes the exact existing M/node dispatch, concrete selections are:

```sh
uv run python tests/helpers/integration_parent.py -- pytest tests/integration/test_human_team_roster_e2e.py::test_c6_exact_manifest_apply_and_preservation -m integration
uv run python tests/helpers/integration_parent.py -- pytest tests/integration/test_human_team_roster_e2e.py::test_c8_preflight_refusals_and_backup_cas -m integration
uv run python tests/helpers/integration_parent.py -- pytest tests/integration/test_human_team_roster_e2e.py::test_c9_crash_recovery_and_replay -m integration
```

There is a precise positive-venue conflict. `containment()` refuses EVERY
undeclared command-bearing system/user service. A usable M would require an
external operator to provide a boot-persistent, independently evidenced closed
launcher inventory in which every command-bearing service is declared and
persistently masked/inactive, with system/user control still genuinely usable.
This source cannot construct that state, mask host units, or accept executable/
unit-name allowlists as exclusion proof. Normal systemd/D-Bus operation can also
encounter descriptor-transfer syscalls rejected by the current observer; no
capability PASS is asserted. Four adversarial conditions (unknown launcher,
foreign owner, actual byte/inode scarcity) require actual external operator
provisioning via the fixture's private request/ready handshake; the handshake
never overrides native observed checks. Actual guest reboot and normal-start
refusal need an external boot/launch witness surviving process loss; they are
not implemented as a fake local signal/restart. These boundaries return to
TASK10394. No infrastructure/host/enforcement mechanism is provisioned here.

Python unit execution/collection/controls/repetitions remain suspended. Only
the finite L/W selections released by TASK10394 may run on disposable GitHub
Ubuntu through the PR1022-only job; its independently pinned b317 selection
renews the old-reader binding to the actual candidate. Required causal and
repetition evidence remains outstanding until genuinely observed;
no live checked manifest exists. Static validation and supported OpenAPI/local
CI are separate source/check evidence, never behavioral QA.


## C10 source and venue boundary

The existing C10 test authors both locales at 390×844 and 1440×900 against the
built SPA. Its valid browser-only pre-attach Product control preserves the shared
Engineering/Content fixtures. Proxy availability and coherent empty Default are
labeled UI projections. Normal model/enrollment/compose/v2 policy actions use
real isolated daemon responses and independent canonical readback. Screenshots
name case/locale/viewport/view/state and assert reachable actions and geometry.
Coherent consultant demotion/cache refresh in mounted Web tests is not an
executed migration. Real after-M browser proof requires the actual C6 M receipt.
TASK10394 releases only the finite hosted L/W/browser/old-reader entry described
in `docs/local-ci.md`. M, compatible restore, reboot, live roster/policy actions
and maintenance remain HELD; source completion supplies no execution receipt.
TASK10476 owns THR291 seq40
Python-unit retirement. Do not resurrect retired source while reconciling main.


## TASK10394 finite hosted proof disposition

Normal nonforce PR1022 publication starts the seven selected L/W shards. They
retain actual commands/exits, source/interpreter/module/tool origins, native
C4 witnesses and final readbacks, C5 positive/refusal observations, C7 emitted
surfaces and C10 screenshots/action/persistence evidence. Independent English/
Chinese screenshot assessment and accepted causal/restoration/repetition proof
still require actual receipts. Early L8/L9 CLI refusals never establish M
no-write or successful check/apply. Existing reviewer TASK10517 BLOCK, prior
failed10430/10452, and every adverse receipt stay attributed to their original
source. Return the bounded outcome to SAME TASK10394; do not claim migration
readiness, overall QA, deployment, live apply or completion.
