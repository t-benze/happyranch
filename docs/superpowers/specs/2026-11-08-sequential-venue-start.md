# U3s venue-only sequential start enablement — SB-1 (a)

Append-only source record, 2026-11-08, TASK-14698 continued by TASK-14700
under ROOT TASK-14640.
Founder THR-336 msg85 approved U1–U9 source and bounded V3; msg102 accepted
SB-1 (a). Engineering_manager step8 resolves only A1 of the TASK-14655 design.
Base: 17c0df0068e749b3560671dbf1a8402c577a7303, including U3b and U3c.
Prior failed tasks and spent allowances remain unchanged. This unit adds no
caller; U3e attachment, V3/V3-0b execution, U4–U9, live installation,
configuration, deployment and restart are outside its scope. Source delivery
is not activation or M1 acceptance. The following retained design contracts
state expected outcomes; actual structural observations live with the PR.

## 1. Enablement mechanism (exact)

Two independent conditions, **both** required for a fresh sequential start. Neither one grants authority. Authority still comes from `WorkflowAuthorityCoordinator` capture/`admission_writer` and U3b's role checks.

### 1.1 Admission record (DB): `Allowed` settings, newest generation

`scripts/setup_sequential_venue.py` (NEW). In one `BEGIN IMMEDIATE` on the venue org DB, while the daemon is stopped, it writes:
- one `workflow_seq_definitions` row (idempotent by `definition_digest`), compiled with the unchanged `sequential_definition.compile_definition(raw, source_digest=…)`, with pins from `compiled.pin`;
- one `workflow_seq_settings` row: `generation = max(existing for (org, identity)) + 1`; `snapshot_bytes = sequential_history._bytes(StorageSettings{format:'workflow-sequential-settings-storage@1', definition: pin, bindings, limits, admission})`;
- one `workflow_seq_settings_operations` row whose request/receipt bytes are exactly the reader's `workflow-sequential-settings-operation-storage@1` / `…-receipt-storage@1` shapes (sequential_history.py:330–347).

Row IDs are deterministic (`sha256` of org/principal/operation_key), following the engine's `_identity` pattern. After COMMIT, the script runs the unchanged `read_sequential_history(conn, expected_org_slug=…)` as an oracle and requires `'storage readable'`. On any oracle refusal it reports a non-zero exit. Rows stay immutable (schema triggers), so a refusal after COMMIT is reported as committed-but-unreadable and is never "repaired".

`--admission Disabled` publishes the next generation as Disabled. Because U3b requires the newest generation, every pin to an older `Allowed` generation is then refused (`settings_generation_stale`).

### 1.2 Venue fence (filesystem): marker in the isolated daemon home

`runtime/workflows/sequential_venue.py` (NEW, SQL-free, no daemon import). `check_venue_start(*, org_slug, org_root, db_path) -> VenueObservation` raises `SequentialVenueUnavailable(code)` unless **all** of these hold, checked in order:

1. `HAPPYRANCH_DAEMON_HOME` is present, absolute, a directory, and equal to its own realpath (no alias).
2. Neither it nor `org_root` is equal to, or inside, the **account's real** `realpath(pwd.getpwuid(os.getuid()).pw_dir)/.happyranch`. This uses the passwd home, **not** `$HOME`, because the venue sets a private HOME. Checks 1 and 2 use only `lstat`/`realpath`. They run before any `open()`.
3. `<daemon_home>/sequential-venue-start.json` is checked with literal no-follow `lstat`. It must be a regular file with `nlink == 1`, owned by the current uid, ≤ 4096 bytes, strict UTF-8 JSON with no duplicate keys and no constants, and exactly the keys `{format:'happyranch-sequential-venue-start@1', daemon_home, runtime_root, org_slug, database_dev, database_ino}`, with the exact types.
4. The marker values equal the current realpaths. `org_root == runtime_root/orgs/<org_slug>`, `org_slug` matches, and `os.stat(db_path)` (dev, ino) matches.

Codes: `sequential_venue_env_absent`, `sequential_venue_live_home`, `sequential_venue_marker_absent`, `sequential_venue_marker_invalid`, `sequential_venue_mismatch`. The public callers (U3e) map every code to one 409 `{'code':'sequential_start_unavailable'}`.

The fence is read **fresh on every fresh start, outside any writer**. It is never cached at startup, so removing the marker disables the next start immediately. It is **not** consulted for callbacks or continuations of an already admitted run, which keep their original pins.

**Ordering inside the script.** Enable: settings COMMIT, then oracle, then marker. The marker is written as an exclusive-create temp file plus a no-follow rename in the daemon home. A prior marker is withdrawn after validation/conflict checks and before new-generation writes, including Allowed republishing. A cooperative advisory directory lock (no lock file) serializes script invocations through post-COMMIT publication. Old-operation replays return their original receipt without changing the current marker decision. Disable: unlink the marker first, then publish the Disabled generation. Every intermediate state fails closed.

**Script preconditions (refuse with zero writes):**
- the same checks 1–2 as the fence;
- `--runtime-root` is listed in `<daemon_home>/runtimes.yaml` and is not a symlink (reuse `resolve_org_database`);
- the daemon is stopped (reuse `scripts.migrate_workflow_submission_schema._lifecycle_snapshot`, as the installer does);
- sequential storage is installed and compatible (`inspect_sequential_install`);
- none of `HAPPYRANCH_RUNTIME_SESSION_ID`, `HAPPYRANCH_ORG_SLUG`, `HAPPYRANCH_TASK_TMP_ROOT` or `HAPPYRANCH_TASK_SCRATCH_MANIFEST` is present. These are the `setup_coding._MARKERS`; the V3 launcher strips them, so their presence means the script is being run from an agent shell, not inside the venue;
- the definition file's sha256 equals `--definition-sha256`;
- the bindings file is closed JSON whose roles equal the definition's roles.

`--confirm-disposable-venue` is required as an operator intent statement. It is **not** proof and grants nothing.

**Not added:** no env var, no `Settings`/config.yaml field, no org config or org_settings key, no route, no CLI verb, no web control, no Founder Start, no change to Coding settings/start (`coding_start*.py` byte-identical; U9 fence unchanged).

## 2. How off-by-default is proven

**Static / lint-level.** Mechanical checks by the reviewer and QA at the exact head; their outputs go in the PR body:
- S1. `git grep -n -E "INSERT INTO workflow_seq_(settings|settings_operations|definitions)\b"` → hits **only** in `scripts/setup_sequential_venue.py`.
- S2. `git grep -n "sequential-venue-start.json"` → the marker is written only by the script. `runtime/` only reads it, in `sequential_venue.py`.
- S3. `git grep -n "setup_sequential_venue\|sequential_venue"` in `runtime/ cli/ web/` → U3s adds no importer. U3e will add exactly the named call sites in §5.
- S4. `git diff <base>...<head> -- runtime/config.py runtime/models.py runtime/daemon cli web org` is empty, and `git grep HAPPYRANCH_SEQUENTIAL` returns nothing. No additional flag exists; the existing daemon-home locator is reused.
- S5. Protected-file SHA256 base/head equality (§3).
- S6. Hosted Source Boundary Lint and Web Lint at the exact head, plus `scripts/local_ci.sh boundary-lint` and `web` exit codes.

No new lint rule is added; S1–S4 are the static proof. THR-291: required checks stay lint/static only.

**Structural (U3s QA, disposable tmp only).** The negative matrix in §4 is run against the real `check_venue_start` and the real script. A U3b admission is shown to commit only with Allowed settings, and to refuse after a Disabled generation.

**V3 (after U3e + V3-0b).** Scenarios:
- **V3-S0:** marker absent. A manager result with a sequential `workflow_start` gets 409 `sequential_start_unavailable`, with zero `workflow_seq_*` run/visit/event rows and zero new tasks.
- **V3-S1:** enabled → exactly one run.
- **V3-S2:** marker removed mid-window → the next start is refused, and the admitted run's callbacks are unaffected.
- **V3-S3:** Disabled generation → `settings_generation_stale`.
- **Teardown receipt (§6):** the live daemon home has no marker before or after.

## 3. Merge-unit placement and exact file radius

| Unit | Base | Radius | Relation |
|---|---|---|---|
| U3c | U3b `54861b02c` | `sequential_engine.py`, `sequential_run_validation.py`, reusable-workflows.md (Finite graph rules + new **U3c** section at end), new `specs/…-u3c.md` | — |
| **U3s (this)** | current main containing U3b `54861b02c` and U3c `17c0df006` | **NEW** `runtime/workflows/sequential_venue.py`; **NEW** `scripts/setup_sequential_venue.py`; `docs/agent-guides/contracts/reusable-workflows.md`: **one new section "## U3s venue-only sequential start enablement (SB-1 a)", inserted immediately after the "## U2b explicit installer/check source" section and before "## U3a"** (no other hunk); **NEW** `docs/superpowers/specs/2026-11-08-sequential-venue-start.md` | Code-disjoint from U3c, U3d-1 and U3d-2. The only shared file is reusable-workflows.md. Its hunk anchor (between U2b and U3a) is separate from the end-of-file sections that U3c, U3d-1 and U3d-2 append, so the squash merges stay textually independent. QA must show `git merge-tree` against **current** main is conflict-free at merge time. If a conflict appears, the PR gets a non-force merge-forward, and review and QA are renewed at the new head (MEM-404/452). |
| U3d-1 | `<U3c merge commit>` | per TASK-14641 u3d1 refresh | U3s files are added to its byte-identical set |
| U3d-2 | `<U3d-1 merge commit>` | per TASK-14625 §1.2 | U3s files are added to its byte-identical set |
| U3e | `<U3d-2 merge commit>` **and** `<U3s merge commit>` | its planned radius plus the §5 call sites | **Serialized after both.** U3e is the only unit that attaches the fence and the settings it reads |

**U3s protected set (byte-identical, SHA256 base/head in the PR body):**
- every U3c/U3d-1/U3d-2 radius file;
- `sequential_{history,reducer,contracts,definition,briefs,engine,run_validation,dispatch}.py`;
- `workflow_schema.py`, `workflow_sequential_schema.py`, `workflow_sequential_ownership.py`, `workflow_task_ownership.py`;
- `authority.py`, every `coding_*.py`/`_coding_*.py`, `models.py`, `run_step.py`, `runtime/config.py`;
- `runtime/daemon/**`, `runtime/orchestrator/**`, `runtime/infrastructure/database.py`, `db/**`;
- `scripts/install_workflow_sequential_schema.py`, `scripts/setup_coding.py`, `scripts/migrate_workflow_submission_schema.py`;
- `runtime/skills/bundled/**`, `cli/**`, `web/**`, task-orchestration.md, runtime-lifecycle.md.

## 4. Negative cases (expected outcome; the U3s structural observation covers rows 1–14; rows 15–19 are V3)

| # | Case | Expected |
|---|---|---|
| 1 | `HAPPYRANCH_DAEMON_HOME` unset | fence `sequential_venue_env_absent`; script refuses, zero writes |
| 2 | env = real account `.happyranch`, or a symlink alias resolving to it, or a path inside it | `sequential_venue_live_home` before any `open()`; script refuses, zero writes; live home remains unopened; only lstat/realpath are permitted in U3s, so content digests are UNOBSERVED |
| 3 | `--runtime-root` = live runtime, or a path not listed in the daemon home's runtimes.yaml, or a symlink | script refuses before opening any DB |
| 4 | marker absent (flag absent), with Allowed settings present | fence `sequential_venue_marker_absent` |
| 5 | marker malformed: invalid UTF-8/JSON, duplicate or extra or missing key, wrong type, > 4096 B, symlink (incl. dangling), hardlink, directory, FIFO, another uid | `sequential_venue_marker_invalid` |
| 6 | marker valid but for another org, runtime or DB inode (copied from another venue) | `sequential_venue_mismatch` |
| 7 | marker present, no settings rows | fence passes; U3b admission refuses (`selected_pin_missing`/`admission_pin_conflict`), zero rows |
| 8 | newer Disabled generation published | U3b `settings_generation_stale`, zero rows |
| 9 | script run while the daemon is running (pid/port present) | refuses, zero writes |
| 10 | script run with agent task markers in env (outside the venue) | refuses, zero writes |
| 11 | definition digest mismatch / bindings ≠ roles / independence violation | refuses before BEGIN; the reader's independence rule is not bypassed |
| 12 | script re-run with the same `--operation-key` and identical request | exact replay: original receipt, zero new rows |
| 13 | same op key with a different request; two concurrent script runs | conflict refusal; concurrent runs serialize under BEGIN IMMEDIATE + UNIQUE(org, identity, generation) → distinct consecutive generations, oracle readable |
| 14 | oracle after each publish | `read_sequential_history` → `storage readable` |
| 15 | V3: sequential `workflow_start` from a non-manager / non-`task` source | existing route 409 `coding_start_manager_result_required`-equivalent gate (U3e reuses the manager check) |
| 16 | V3: concurrent start (two deliveries of the same manager result) | one run; DDL UNIQUE(org, source_principal, start_operation_key) + UNIQUE(source_task_id) + UNIQUE(source_result_id) under `admission_writer`; second = exact replay with original IDs or `start_operation_conflict` |
| 17 | V3: repeated start (re-report after admission; new result with the same op key; same source after the run ends) | zero duplicate run/carrier/visit/event/outbox rows, counted by SQL |
| 18 | V3: marker removed mid-run | the next fresh start is refused; the in-flight run's callbacks proceed (the fence is never consulted on continuations) |
| 19 | V3: teardown | §6 receipt; a retained (UNRESOLVED) run still has the marker removed first |

## 5. U3e attachment contract (paste into the U3e brief; not part of U3s)

- `models.py` `NextStep._coding_start_is_closed`: when `workflow.format == 'workflow-sequential-start@1'`, validate a closed `SequentialStartDecision` (in a sequential module, **not** `coding_start_records.py`, which stays byte-identical). Every other `workflow_start` stays `CodingStartDecision`, unchanged.
- `routes/tasks.py:submit_completion`: next to `:1194`, keep the same manager-result check. Next to `:1492`, for a legacy owner with a sequential `workflow_start`, call `check_venue_start(...)` **and** a read-only settings pre-check. Both run before `admit_task_completion_callback`, at the Coding position. Map refusals to 409 `{'code':'sequential_start_unavailable'}` with zero persistence. Order 404 → classify → 409 is unchanged (PR1262).
- `run_step.py:2854` consumer: route the sequential format to the sequential start consumer. **Re-run `check_venue_start` fresh** before `admission_writer`. Then capture, then `admission_writer(capture)` → U3b `admit_uncommitted` → COMMIT. A refusal logs and holds, with zero writes. There is no file I/O inside the writer.
- No fence call on callbacks, transitions, return or cancel.

## 6. V3 teardown disables it (V3-0b additions)

1. **Before stop:** the launcher unlinks `<run>/dh/sequential-venue-start.json` (literal no-follow) and records the removal. Even a stray, unreaped daemon then refuses new starts.
2. Spec §7 steps 1–4 are unchanged: SIGTERM → grace → SIGKILL → ECHILD, then delete `<run>`, which removes the venue DB, its settings rows and the daemon home.
3. **Receipt adds:** marker digest at enable time; removal time; `lstat(<real account>/.happyranch/sequential-venue-start.json)` = ENOENT before and after the run (metadata only); existing live `executors.json`/`runtimes.yaml` digests unchanged.
4. If teardown is incomplete, the UNRESOLVED rules are unchanged; the marker was already removed in step 1.

**V3-0b scope:**
- closed new scenario step kinds `daemon_stop`, `daemon_start`, `operator`;
- `operator` is allowlisted to exactly `scripts/install_workflow_sequential_schema.py` and `scripts/setup_sequential_venue.py` from `<run>/src`, run with the venv python `-I`;
- arguments come from a closed per-script template; `--runtime-root` must be `${RUN}/rt` and the org must be `v3`;
- the marker-removal teardown step and the receipt fields.

Every other invariant stays byte-preserved (exact env equality, PATH, clone, Git isolation, port 0, subreaper/ECHILD, UNRESOLVED). V3-0b's EMPTY acceptance run opens the 14-day window, so **schedule it only when U3e is at review**.

## 7. Interpretations stated (not founder findings)

- I1. "No public route" is read as **no new route, CLI verb, UI or credential**. U3e lets the existing manager `report-completion` decision union carry the closed sequential start format, which is refused everywhere except a fenced venue. This is the SB-1 (a) text the founder accepted ("consumed by the existing `workflow_start` consumer").
- I2. Writing settings rows in the **disposable venue DB** is not "live config/admission" or a live install. It is inside the approved V3 (spec §2: storage installed through U2's installer in the venue).
- I3. V3-0b is a script revision under spec §4 (fresh independent code_reviewer APPROVE, no founder decision).
- I4. The fence is a restriction, never authority. U7/U9 later replace it with real settings/admission; that is out of scope here.

## 8. Risks, assumptions and source contract additions

V1 (engineering_manager TASK-14640 step9) supersedes the original structural
task-tmp venue instruction only. Create `mktemp -d
/var/tmp/hr-u3s-<own-task-id>-XXXXXX`: realpath must equal the literal path,
outside the entire passwd home, owned by the invoking UID with mode 0700.
Use only fixture runtime/daemon-home trees, fresh initialized org DBs and
backups; no live-home content copy, daemon loop, executor or job. Do not use
`/tmp`, symlink/mount/bind, UID/HOME changes or environment overrides of the
live-home fence. Probe real-home paths with lstat/realpath only. Save concise
evidence under the actual task output, then remove only the exact mktemp
directory after checking safety; record path and removal exit or retention
reason. No V3/V3-0b/genuine Coding scratch follows. A future V3 venue must also
sit outside the real account's `.happyranch`. The fence itself is unchanged.

The shipped script requires the arguments listed in the U3s guide section,
including optional `--canonical-sha256`. Bindings-file is a JSON array of closed
`{role,principal}` objects; limits-file is the closed Limits object. Duplicate
JSON keys/constants refuse. Supplied limits cannot exceed the definition.
A2 imports remain read-only. No test-only seam, compiler change or shared
extractor is introduced. Definition IDs derive from canonical digest; settings
and operation IDs use the engine's SHA256 encoding pattern over org/operator/key.
Exact operation replay compares the full snapshot and returns original receipt.
Exit codes: 0 success; 1 refusal, including an explicitly diagnosed committed
post-COMMIT failure; 2 usage. Committed bytes are never repaired.

Additional A1 structural matrix: source-digest mismatch, canonical-digest
mismatch, missing LF, double LF, CRLF, symlinked definition, oversize definition:
each exit 1 with zero rows written. All four reviewed files extract to their
README canonical digests; Draft/Review and Coding v2 traverse the real full
publish path. These are structural observations only, never V3 behavioral PASS.

A3: the directory advisory lock coordinates these operator invocations only;
it is neither arbitrary-writer exclusion nor historical cleanup proof. A4:
live-home alias refusal is classified as live_home before the general alias
refusal, using metadata only. A5: no real-home content digest is acquired in
this unit because the task permits only lstat/realpath there. V3's future
teardown receipt still owns its broader digest requirements.



- A1' (EM TASK-14640 step8). The publisher itself is the explicit bounded reader: regular no-follow file <=1 MiB; require source-file SHA256, exactly one final LF and no CRLF; canonical = raw[:-1] with no further change. Compile with the unchanged compiler; print canonical SHA256. Optional --canonical-sha256 must match. Every mismatch exits 1, zero rows.
- A2. `StorageSettings` and `_bytes` are imported read-only from `sequential_history.py`, which is not edited. If importing a module-private name is refused by review, STOP and report rather than duplicating the encoder.
- R1. Disabling an in-flight venue run: if U3c scopes call `_pins` (with the newest-generation check), a Disabled generation also holds continuations. That is acceptable for the venue, but it conflicts with the "continuations keep original pins" wording. **Reviewer focus for U3c/U3e, not U3s.** For V3-S2, disable by removing the marker only.
- R2. U3e is now serialized after U3s **and** U3d-2.
- R3. The local primary checkout is stale and did not fast-forward. Workers must branch from fetched origin/main.

---
