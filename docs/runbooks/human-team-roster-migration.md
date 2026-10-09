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
   private durable operation_dir outside runtime/workspaces/tmp, exact closed
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
/venue/old-b317/.venv/bin/python -I /venue/candidate/tests/helpers/human_team_incompatible_reader_probe.py --source /venue/old-b317 --source-sha b3179b123fddbb0f0f604ed9e0d148f1b23455f3 --root /venue/reader-case --org reader-case --operation capture-admission --expect workflow_activation_authority_stale --snapshot-digest ACTUAL_SCHEMA2_DIGEST
```

Bind an isolated mode-0700 reader daemon home and Python3.14. The helper uses
actual reader constructors, unfenced public profile binding and native recovery
before measurement; it verifies module origins and input digest. It does not
republish schema1, replace a validator or manufacture a graph/receipt. Its exact
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

C7's closed test-side context plan is authored at
`tests/helpers/human_team_context_plan.py`. Existing guarded fake binaries pass
their full actual stdin and capture original argv without changing positional
plan conventions or usage output. The helper binds genuine task/session,
thread token/trigger sequence, dream, wake and schedule IDs; it calls the
corresponding supported callback and records actual generated settings,
instruction bytes and both skill-root links. It does not produce or settle a
context, insert a result, or attest final behavior. Ten real producer/admission
cases are authored in `test_c7_both_resume_resets_and_worker_contexts`, one per
consultant and task/thread/dream/wake/schedule. They independently check the
native resets, retained memory and archived delivery/breaker controls, full
thread context, actual callback and durable context/task outcome. Their L
fixture resets do not attest successful operator migration in M. Execution,
causal controls and five repetitions remain unavailable; source alone does not
close C7. Wbrowser's finite selection is now authored at
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
cuts, logical-line localization and full capability/loss/kill controls still
require closure; an unsupported cut or observation is UNAVAILABLE, never PASS.
No helper was run on this host. Zero-row/already-null replay and M reboot proof
remain outstanding even when final file hashes or audit counts agree.
