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
or adopted. Native creation-mask provenance is rechecked before materialization.
