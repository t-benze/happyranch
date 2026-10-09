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
   systemd user-unit and registry paths bound to the effective `daemon_home`, and independently inspected exact
   generated after-images from unchanged candidate materializers. Every image
   names kind, mode, uid/gid, base64 bytes and SHA256; links retain raw targets.
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
