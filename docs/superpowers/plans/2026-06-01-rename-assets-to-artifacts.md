# Rename `assets` → `artifacts` and per-agent `artifacts/` → `output/` Implementation Plan

> **Status — 2026-06-11: IMPLEMENTED.** The backend/CLI/migration/audit half of the `assets` → `artifacts` rename is merged to `main` (confirmed by the TASK-104 reconciliation). The web-frontend half — `web/src/features/assets/` → `features/artifacts/`, `AssetsPage` → `ArtifactsPage`, the `/orgs/:slug/artifacts` SPA route, and doc parity — landed on PR #78 (branch `task/TASK-070`) via TASK-113. This plan is retained as implementation history.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the org-shared blob-store feature from `assets` to `artifacts`, and rename the per-agent task-output convention from `artifacts/<task_id>/` to `output/<task_id>/`. The two renames are non-colliding because Phase 1 vacates the name `artifacts` before Phase 2 claims it for the org-shared store.

**Architecture:**

- Phase 1 first: rename per-agent `artifacts/` convention → `output/` (DB columns, Pydantic fields, CLI flags, query params, agent skill convention strings, the `_read_artifact` helper, and the `MAX_ARTIFACT_BYTES` constant in `routes/tasks.py`). Frees up `artifacts/` and the `artifact*` namespace.
- Phase 2 then: rename `assets` → `artifacts` (Python module/file `asset_store.py` → `artifact_store.py`, route module `routes/assets.py` → `routes/artifacts.py`, classes `AssetStore`/`AssetInfo`/etc., CLI subcommand `happyranch assets …`, on-disk path `<runtime>/orgs/<slug>/assets/` → `artifacts/`, audit action `asset_put` → `artifact_put`, task_id prefix `asset:` → `artifact:`, error codes, the `MAX_ASSET_BYTES` constant, and the `OrgPaths.assets_dir` helper).
- Phase 3: one-shot migration sweep (SQL + shell). Follows the `2026-06-01_drop_close_out_columns.{sql,sh}` pattern in `scripts/migrations/`: stop the daemon, iterate every `<runtime>/orgs/*/happyranch.db`, run SQL, also `mv` on-disk dirs.
- Phase 4: docs/specs/skills/CLAUDE.md/README + OpenAPI snapshot regen + web `INCLUDED`/`EXCLUDED` paths.

Audit log is forward-only — old `asset_put` rows and `asset:<name>` task_ids stay as-is in the append-only audit log; new writes use `artifact_put` and `artifact:<name>`. CLI flags rename outright (no aliases), consistent with the project's clean-break pattern (cf. thread close-out removal).

**Tech Stack:** Python 3.13, FastAPI, SQLite (per-org `<runtime>/orgs/<slug>/happyranch.db`), uv, pytest. TypeScript only for the OpenAPI-coverage test mirror.

---

## Design Notes (read before starting)

### Why Phase 1 before Phase 2

The names don't collide at the symbol level (different identifiers and paths), but doing Phase 1 first makes the diff easier to reason about: after Phase 1, every remaining occurrence of `artifact`/`asset` in source is genuinely about the org-shared store and can be mass-renamed in Phase 2 without ambiguity.

There's also one literal collision to handle inside Phase 1: `routes/tasks.py` defines a constant `MAX_ARTIFACT_BYTES` (the size cap for the per-agent recall reader). If we left that as-is and then renamed `MAX_ASSET_BYTES` → `MAX_ARTIFACT_BYTES` in Phase 2, we'd have two unrelated constants with the same name. So Phase 1 also renames `MAX_ARTIFACT_BYTES` → `MAX_OUTPUT_BYTES` and the `_read_artifact` helper → `_read_output`, and the `payload["artifact"]` recall key → `payload["output"]`.

### Forward-only audit log

`audit_log` is append-only project-wide. Old rows keep their original `action` and `task_id` values:

- `action="asset_put"` rows from the old code path stay as `asset_put`.
- `task_id="asset:<name>"` overload values stay with the `asset:` prefix.

New writes after Phase 2 use `action="artifact_put"` and `task_id="artifact:<name>"`. Founder browsing old audit will see both — acceptable (the audit log already mixes legacy action names from earlier migrations).

This means the migration **does not** UPDATE the `audit_log` table.

### CLI flag rename (no aliases)

- `--artifact-dir` → `--output-dir` (on `happyranch report-completion`)
- `--fetch-artifact` → `--fetch-output` (on `happyranch recall`)
- Query param `include_artifact` on `GET /tasks/{task_id}/recall` → `include_output`

Old flag names are removed entirely. Agent skills are updated in lockstep.

### Migration shape

Following the `2026-06-01_drop_close_out_columns` precedent (`scripts/migrations/`):

- `2026-06-01_rename_assets_to_artifacts.sql` — per-org DB: `ALTER TABLE … RENAME COLUMN` and `UPDATE … SET … = REPLACE(…, 'artifacts/', 'output/')` on stored path strings.
- `2026-06-01_rename_assets_to_artifacts.sh` — iterates `<runtime>/orgs/*/happyranch.db`, runs the SQL against each. Also performs filesystem moves: `<runtime>/orgs/*/assets/` → `<runtime>/orgs/*/artifacts/` and `<runtime>/orgs/*/workspaces/*/artifacts/` → `<runtime>/orgs/*/workspaces/*/output/` (only when source exists, idempotent if already moved).

Daemon must be stopped before running.

### Files renamed (not just edited)

- `src/infrastructure/asset_store.py` → `src/infrastructure/artifact_store.py`
- `src/daemon/routes/assets.py` → `src/daemon/routes/artifacts.py`
- `tests/test_asset_store.py` → `tests/test_artifact_store.py`
- `tests/test_cli_assets.py` → `tests/test_cli_artifacts.py`
- `tests/daemon/test_assets_routes.py` → `tests/daemon/test_artifacts_routes.py`
- `tests/integration/test_assets_e2e.py` → `tests/integration/test_artifacts_e2e.py`

Use `git mv` so history is preserved.

### Commit cadence

Each task ends with a commit (frequent commits, small diffs). Phase 1 should be a self-contained green-tests state before Phase 2 begins.

---

## File Structure (post-rename)

```
src/
  infrastructure/
    artifact_store.py             # was asset_store.py
    audit_logger.py               # log_artifact_put (new name); old log_asset_put deleted
  daemon/
    routes/
      artifacts.py                # was assets.py
      tasks.py                    # _read_output (was _read_artifact), MAX_OUTPUT_BYTES
  orchestrator/
    _paths.py                     # OrgPaths.artifacts_dir (was assets_dir)
    workspace_adapters.py         # _shared_artifacts_section (was _shared_assets_section)
    chain.py                      # build_prior_leg_context: "Output dir: ..." (was "Artifact dir: ...")
    run_step.py                   # _complete(..., output_dir=...) (was artifact_dir)
    orchestrator.py               # task summary: "Output: ..." (was "Artifact: ...")
  cli.py                          # `happyranch artifacts {put,list,get}`, --output-dir, --fetch-output
  client/client.py                # put_artifact, list_artifacts, get_artifact
  models.py                       # CompletionReport.output_dir, TaskRecord.final_output_dir

tests/
  test_artifact_store.py          # was test_asset_store.py
  test_cli_artifacts.py           # was test_cli_assets.py
  daemon/test_artifacts_routes.py # was test_assets_routes.py
  integration/test_artifacts_e2e.py
  # + all other test files updated in-place

scripts/migrations/
  2026-06-01_rename_assets_to_artifacts.sql
  2026-06-01_rename_assets_to_artifacts.sh

docs/superpowers/plans/
  2026-06-01-rename-assets-to-artifacts.md   # this file
```

---

## Phase 1 — Per-agent `artifacts/<task_id>/` → `output/<task_id>/`

### Task 1.1: Rename Pydantic fields

**Files:**
- Modify: `src/models.py:52` (TaskRecord)
- Modify: `src/models.py:107` (CompletionReport)
- Test: `tests/test_models.py:122-134`

- [ ] **Step 1: Update the failing test for CompletionReport**

In `tests/test_models.py`, replace lines 122-134:

```python
def test_completion_report_accepts_output_dir():
    r = CompletionReport(
        task_id="T", session_id="S", agent="dev_agent", status="completed",
        confidence=80, output_summary="done", output_dir="output/TASK-001",
    )
    assert r.output_dir == "output/TASK-001"


def test_completion_report_output_dir_defaults_to_none():
    r = CompletionReport(
        task_id="T", session_id="S", agent="dev_agent", status="completed",
        confidence=80, output_summary="done",
    )
    assert r.output_dir is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_models.py -v
```

Expected: FAIL — `output_dir` not on `CompletionReport`.

- [ ] **Step 3: Rename Pydantic fields**

In `src/models.py`:
- Line 52: `final_artifact_dir: str | None = None` → `final_output_dir: str | None = None`
- Line 107: `artifact_dir: str | None = None` → `output_dir: str | None = None`
- Line 268: update the docstring comment referencing `asset_put`/`artifact_dir` pattern — for this task only touch the `artifact_dir` mention; the `asset_put` mention belongs to Phase 2.

- [ ] **Step 4: Run model test**

```bash
uv run pytest tests/test_models.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "refactor(models): rename artifact_dir → output_dir, final_artifact_dir → final_output_dir"
```

---

### Task 1.2: Rename DB columns and persistence

**Files:**
- Modify: `src/infrastructure/database.py` (lines 213, 239, 420-421, 692, 760, 869, 907, 920, 1268, 1277, 1293, 1383)
- Test: `tests/test_database.py:212-309, 751, 793`

- [ ] **Step 1: Update test_database.py expected column names**

Replace every `final_artifact_dir` with `final_output_dir`, every `artifact_dir` with `output_dir` in these tests:
- `test_insert_task_result_stores_artifact_dir` → `test_insert_task_result_stores_output_dir`
- `tests/test_database.py:212-228` (assertions + inputs)
- `tests/test_database.py:263-309` (assertions + inputs)
- `tests/test_database.py:751` (schema literal `final_artifact_dir TEXT`)
- `tests/test_database.py:793` (schema literal `artifact_dir TEXT`)

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_database.py -v -k "output_dir or final_output_dir"
```

Expected: FAIL — columns don't exist by new name.

- [ ] **Step 3: Update schema definitions, migration list, and queries**

In `src/infrastructure/database.py`:
- Line 213 (tasks table CREATE): `final_artifact_dir TEXT` → `final_output_dir TEXT`
- Line 239 (task_results table CREATE): `artifact_dir TEXT,` → `output_dir TEXT,`
- Lines 420-421 (idempotent ALTER list): `"ALTER TABLE tasks ADD COLUMN final_artifact_dir TEXT"` → `"ALTER TABLE tasks ADD COLUMN final_output_dir TEXT"` and `"ALTER TABLE task_results ADD COLUMN artifact_dir TEXT"` → `"ALTER TABLE task_results ADD COLUMN output_dir TEXT"`
- Line 692: `final_artifact_dir=row["final_artifact_dir"]` → `final_output_dir=row["final_output_dir"]`
- Line 760: same
- Line 869: `"artifact_dir": task.final_artifact_dir,` → `"output_dir": task.final_output_dir,` (this is a recall_payload key — see Task 1.4 for the `_read_artifact` helper's lookup)
- Line 907: same as 692/760
- Line 920: in the explicit column list, replace `"final_artifact_dir"` with `"final_output_dir"`
- Line 1268 (`insert_task_result` signature): `artifact_dir: str | None = None,` → `output_dir: str | None = None,`
- Line 1277 (INSERT column list): `... artifact_dir, ...` → `... output_dir, ...`
- Line 1293 (INSERT bind tuple): `artifact_dir,` → `output_dir,`
- Line 1383 (row mapper): `artifact_dir=row["artifact_dir"] if "artifact_dir" in keys else None,` → `output_dir=row["output_dir"] if "output_dir" in keys else None,`

- [ ] **Step 4: Run database tests**

```bash
uv run pytest tests/test_database.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "refactor(db): rename artifact_dir / final_artifact_dir columns to output variants"
```

---

### Task 1.3: Rename in run_step, chain, orchestrator, thread_store

**Files:**
- Modify: `src/orchestrator/run_step.py` (lines 314, 972-985, 1705)
- Modify: `src/orchestrator/chain.py` (lines 123-125)
- Modify: `src/orchestrator/orchestrator.py` (lines 370, 583-584)
- Modify: `src/infrastructure/thread_store.py` (lines 100-102)
- Test: `tests/test_run_step.py`, `tests/test_chain.py`, `tests/test_orchestrator.py`, `tests/test_thread_task_followup.py`

- [ ] **Step 1: Update tests in lockstep**

In `tests/test_run_step.py`:
- Line 116: `artifact_dir: str | None = None` → `output_dir: str | None = None`
- Line 120: keyword arg `artifact_dir=` → `output_dir=`
- Line 175: `artifact_dir="artifacts/run-1"` → `output_dir="output/run-1"`
- Line 184: `child.final_artifact_dir == "artifacts/run-1"` → `child.final_output_dir == "output/run-1"`
- Line 767, 777: same shape
- Line 933: `_complete(orch, "T-1", note="looks great", artifact_dir="artifacts/run-1")` → `_complete(orch, "T-1", note="looks great", output_dir="output/run-1")`
- Line 938: `t.final_artifact_dir is None` → `t.final_output_dir is None`

In `tests/test_chain.py`:
- Line 46: `artifact_dir="workspaces/senior_dev/artifacts/TASK-579/",` → `output_dir="workspaces/senior_dev/output/TASK-579/",`
- Line 54: `"Artifact dir: workspaces/senior_dev/artifacts/TASK-579/"` → `"Output dir: workspaces/senior_dev/output/TASK-579/"`
- Line 57: `test_build_prior_leg_context_omits_artifact_dir_when_unset` → `..._omits_output_dir_when_unset`

In `tests/test_orchestrator.py:386-401`:
- Function name `test_read_completion_from_db_preserves_artifact_dir` → `..._preserves_output_dir`
- Docstring + assertions: replace `artifact_dir` → `output_dir`, `"artifacts/TASK-001"` → `"output/TASK-001"`

In `tests/test_thread_task_followup.py`:
- Lines 147, 169, 245: `"final_artifact_dir":` → `"final_output_dir":` (also update the `/reports/TASK-7/` path string if needed — keep it as-is, it's already non-`artifacts`)
- Line 668: `artifact_dir=None` → `output_dir=None`

- [ ] **Step 2: Update run_step.py**

- Line 314: `artifact_dir=report.artifact_dir,` → `output_dir=report.output_dir,`
- Lines 972-985 (the `_complete` helper): rename parameter `artifact_dir` → `output_dir`, and the call `final_artifact_dir=artifact_dir` → `final_output_dir=output_dir`. Update any local docstring.
- Line 1705: `"final_artifact_dir": terminal_task.final_artifact_dir,` → `"final_output_dir": terminal_task.final_output_dir,`

- [ ] **Step 3: Update chain.py**

In `src/orchestrator/chain.py`, lines 123-125 of `build_prior_leg_context`:

```python
    if report.output_dir:
        lines.append("")
        lines.append(f"Output dir: {report.output_dir}")
```

- [ ] **Step 4: Update orchestrator.py**

- Line 370: `artifact_dir=row.get("artifact_dir"),` → `output_dir=row.get("output_dir"),`
- Lines 583-584 (task summary rendering):

```python
            if t.final_output_dir:
                lines.append(f"  - Output: `{t.final_output_dir}`")
```

- [ ] **Step 5: Update thread_store.py**

In `src/infrastructure/thread_store.py`, lines 100-102, replace `final_artifact_dir` lookup with `final_output_dir` and rename the local variable for clarity:

```python
                output = payload.get("final_output_dir")
```

(adjust the surrounding f-string label `"Artifact"` → `"Output"` if present)

- [ ] **Step 6: Run the affected unit tests**

```bash
uv run pytest tests/test_run_step.py tests/test_chain.py tests/test_orchestrator.py tests/test_thread_task_followup.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/ src/infrastructure/thread_store.py tests/test_run_step.py tests/test_chain.py tests/test_orchestrator.py tests/test_thread_task_followup.py
git commit -m "refactor(orchestrator): rename artifact_dir → output_dir across run_step, chain, orchestrator, thread_store"
```

---

### Task 1.4: Rename `_read_artifact` helper + `MAX_ARTIFACT_BYTES` + `include_artifact` query param

**Files:**
- Modify: `src/daemon/routes/tasks.py` (lines 181-236, 272, 360, plus the `MAX_ARTIFACT_BYTES` constant definition)
- Modify: `src/infrastructure/database.py:869` (recall_payload key — already done in Task 1.2 but verify)
- Test: `tests/daemon/test_routes_tasks.py:304-327, 529-536`

- [ ] **Step 1: Find the MAX_ARTIFACT_BYTES definition**

```bash
grep -n 'MAX_ARTIFACT_BYTES' src/daemon/routes/tasks.py
```

Expected: the constant is defined near the top of `routes/tasks.py`. Note its value (likely something like `512 * 1024`).

- [ ] **Step 2: Update test in lockstep**

In `tests/daemon/test_routes_tasks.py`:
- Line 304: function name `test_completion_persists_artifact_dir` → `..._persists_output_dir`
- Line 321: payload key `"artifact_dir": f"artifacts/{task_id}"` → `"output_dir": f"output/{task_id}"`
- Line 327: assertion `rows[-1]["artifact_dir"] == f"artifacts/{task_id}"` → `rows[-1]["output_dir"] == f"output/{task_id}"`
- Line 529: `final_artifact_dir="artifacts/TASK-001"` → `final_output_dir="output/TASK-001"`
- Line 536: `body["artifact_dir"] == "artifacts/TASK-001"` → `body["output_dir"] == "output/TASK-001"`

Also search the file for `include_artifact`, `_read_artifact`, `_recall_node` query-param usages and update.

- [ ] **Step 3: Rename in routes/tasks.py**

- Constant: `MAX_ARTIFACT_BYTES` → `MAX_OUTPUT_BYTES` (all references, including the docstring at line 187)
- Function `_read_artifact` → `_read_output`; parameter `artifact_dir: str | None` → `output_dir: str | None`; payload `payload.get("artifact_dir")` → `payload.get("output_dir")`
- `_recall_node`: parameter `include_artifact: bool` → `include_output: bool`; result key `payload["artifact"]` → `payload["output"]`
- Lines 220-236: update the helper call site and the `payload["artifact"] = …` assignment
- Line 272: pydantic body field `artifact_dir: str | None = None` → `output_dir: str | None = None`
- Line 360: `artifact_dir=body.artifact_dir,` → `output_dir=body.output_dir,`
- The route handler at line 246 (`/tasks/{task_id}/recall`): query param `include_artifact: bool = False` → `include_output: bool = False`

- [ ] **Step 4: Run tasks-route tests**

```bash
uv run pytest tests/daemon/test_routes_tasks.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "refactor(routes): rename _read_artifact → _read_output, MAX_ARTIFACT_BYTES → MAX_OUTPUT_BYTES, include_artifact → include_output"
```

---

### Task 1.5: Rename CLI flags + payload key

**Files:**
- Modify: `src/cli.py` (lines 657-658, 723-724, 2746-2747, 1545-1546)
- Modify: `src/client/client.py` (look for `include_artifact` query param construction in the recall method)
- Test: `tests/test_cli.py:445-473`, `tests/test_skills.py:52`

- [ ] **Step 1: Update CLI tests**

In `tests/test_cli.py`:
- Line 445: function name `test_completion_payload_from_file_accepts_artifact_dir` → `..._accepts_output_dir`
- Line 456: payload key `"artifact_dir": "artifacts/TASK-001"` → `"output_dir": "output/TASK-001"`
- Line 460: `body["artifact_dir"] == "artifacts/TASK-001"` → `body["output_dir"] == "output/TASK-001"`
- Line 473: `body.get("artifact_dir") is None` → `body.get("output_dir") is None`

In `tests/test_skills.py:52`: assertion `"artifact_dir" in body` → `"output_dir" in body`.

- [ ] **Step 2: Rename in src/cli.py**

- Lines 657-658 (in `cmd_report_completion` payload-from-file handling):

```python
    if data.get("output_dir"):
        body["output_dir"] = data["output_dir"]
```

- Lines 723-724 (CLI-arg fallback):

```python
        if args.output_dir:
            body["output_dir"] = args.output_dir
```

- Lines 2746-2747 (argparse setup):

```python
    p_rep.add_argument("--output-dir", dest="output_dir", default=None,
                       help="Relative path to the output directory under the agent workspace")
```

- Lines 1545-1546 (recall command): `args.fetch_artifact` → `args.fetch_output`, and the query param key passed to the client → `include_output`.

Search for any other `--fetch-artifact`, `fetch_artifact`, `include_artifact`, `artifact_dir` strings in `src/cli.py` and rename consistently. Look near line 2746 for the `--fetch-artifact` definition on `p_recall` (or similar) and rename to `--fetch-output`.

- [ ] **Step 3: Update client.py**

In `src/client/client.py`, find the recall method (likely `get_recall` or `recall_task`) and rename its `include_artifact` parameter / query string to `include_output`.

- [ ] **Step 4: Run CLI tests**

```bash
uv run pytest tests/test_cli.py tests/test_skills.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py src/client/client.py tests/test_cli.py tests/test_skills.py
git commit -m "refactor(cli): rename --artifact-dir → --output-dir, --fetch-artifact → --fetch-output, include_artifact → include_output"
```

---

### Task 1.6: Update agent prompt convention strings

**Files:**
- Modify: `protocol/skills/start-task/SKILL.md` (lines 63, 139)
- Modify: `protocol/skills/thread/SKILL.md` (line 141 if it references artifact_dir)
- Modify: `protocol/00-completion-contract.md` (line 30 example, line 136 "Prior leg context")
- Modify: `src/orchestrator/workspace_adapters.py` (lines 585-590 recall hint)

- [ ] **Step 1: Update workspace_adapters.py recall hint**

In `src/orchestrator/workspace_adapters.py`, lines 585-590, replace with:

```python
            "## Task Recall\n",
            "Past task context (brief, completion summary, output files) is retrievable via:",
            "```",
            "happyranch recall <task_id>                            # brief + final summary",
            "happyranch recall <task_id> --tree                     # list files under output/<task_id>/",
            "happyranch recall <task_id> --fetch-output <relpath>   # read one output file",
            "```",
```

- [ ] **Step 2: Update protocol/skills/start-task/SKILL.md**

- Line 63: change "write its files under `artifacts/<task_id>/` in your workspace root" → "write its files under `output/<task_id>/` in your workspace root"
- Line 139: in the example completion payload, change `"artifact_dir": "artifacts/<task_id>"` → `"output_dir": "output/<task_id>"`

Search the file for any other `artifact` / `artifacts` references and update.

- [ ] **Step 3: Update protocol/skills/thread/SKILL.md if needed**

```bash
grep -n 'artifact' protocol/skills/thread/SKILL.md
```

Rename any `artifact_dir` / `artifacts/<task_id>/` references to `output_dir` / `output/<task_id>/`.

- [ ] **Step 4: Update protocol/00-completion-contract.md**

- Line 30: example payload `"artifact_dir": "artifacts/<task_id>"` → `"output_dir": "output/<task_id>"`
- Line 136 ("Prior leg context"): rename `artifact_dir` → `output_dir`; rename the "Artifact dir:" label in the rendered chain context to "Output dir:".

- [ ] **Step 5: Sanity-grep for missed `artifact_dir` / `artifacts/<task_id>` strings under protocol/ and src/**

```bash
grep -rIn -e 'artifact_dir' -e 'artifacts/<task' -e 'artifacts/{task' protocol/ src/ skills/ 2>/dev/null
```

Expected: empty (or only the Phase 2 `artifacts` feature references that stay). If anything remains under Phase 1's scope, fix it.

- [ ] **Step 6: Commit**

```bash
git add protocol/ src/orchestrator/workspace_adapters.py
git commit -m "docs(protocol): rename artifacts/<task_id>/ → output/<task_id>/ in agent skill conventions"
```

---

### Task 1.7: Phase 1 full unit-test green

- [ ] **Step 1: Run the full unit suite**

```bash
uv run pytest tests/ -v
```

Expected: PASS (no integration yet; we re-run integration after Phase 3's migration is in place).

If anything red, search the failure output for stragglers (`artifact_dir`, `final_artifact_dir`, `include_artifact`, `--fetch-artifact`, `--artifact-dir`) and fix.

- [ ] **Step 2: Tag the Phase 1 boundary**

```bash
git tag phase-1-output-rename
```

(Local tag; not pushed.)

---

## Phase 2 — `assets` → `artifacts` (org-shared store)

### Task 2.1: Rename the storage module + classes

**Files:**
- Rename: `src/infrastructure/asset_store.py` → `src/infrastructure/artifact_store.py` (git mv)
- Modify: the new file's class names, constants, error types

- [ ] **Step 1: git mv the module**

```bash
git mv src/infrastructure/asset_store.py src/infrastructure/artifact_store.py
```

- [ ] **Step 2: Rename inside the file**

Replace every occurrence:
- `MAX_ASSET_BYTES` → `MAX_ARTIFACT_BYTES`
- `InvalidAssetName` → `InvalidArtifactName`
- `AssetTooLarge` → `ArtifactTooLarge`
- `AssetNotFound` → `ArtifactNotFound`
- `AssetInfo` → `ArtifactInfo`
- `AssetStore` → `ArtifactStore`
- `list_assets` method → `list_artifacts`
- Docstrings: "asset" → "artifact" (mind capitalization), "Org-shared asset storage" → "Org-shared artifact storage", error message strings ("invalid_name", "asset_too_large" — leave the latter; that error string moves to Task 2.2 in the route).
- The internal validation error string `f"asset_too_large: …"` → `f"artifact_too_large: …"`

Use `replace_all` in Edit; double-check by reading the whole file after.

- [ ] **Step 3: Defer commit until Task 2.2**

(Imports across the codebase will break until route + tests catch up; commit those together.)

---

### Task 2.2: Rename the route module

**Files:**
- Rename: `src/daemon/routes/assets.py` → `src/daemon/routes/artifacts.py` (git mv)
- Modify: imports, route paths, function names, error codes
- Modify: `src/daemon/app.py` (lines 11, 98, 162)
- Modify: `src/daemon/routes/orgs.py:54`

- [ ] **Step 1: git mv the route**

```bash
git mv src/daemon/routes/assets.py src/daemon/routes/artifacts.py
```

- [ ] **Step 2: Rewrite the new file**

In `src/daemon/routes/artifacts.py`:
- Module docstring: "Org-shared assets routes" → "Org-shared artifacts routes"
- Imports: `from src.infrastructure.asset_store import (MAX_ASSET_BYTES, AssetStore, InvalidAssetName,)` → `from src.infrastructure.artifact_store import (MAX_ARTIFACT_BYTES, ArtifactStore, InvalidArtifactName,)`
- `_store(org) -> AssetStore` → `_store(org) -> ArtifactStore`; body uses `OrgPaths(org.root).artifacts_dir` (renamed in Task 2.3)
- Route paths:
  - `@router.post("/assets")` → `@router.post("/artifacts")`
  - `@router.get("/assets")` → `@router.get("/artifacts")`
  - `@router.get("/assets/{name}")` → `@router.get("/artifacts/{name}")`
- Function names: `put_asset` → `put_artifact`, `list_assets` → `list_artifacts`, `get_asset` → `get_artifact`
- Error codes (in HTTPException detail bodies):
  - `"asset_too_large"` → `"artifact_too_large"`
  - `"invalid_asset_name"` → `"invalid_artifact_name"`
  - `"asset_not_found"` → `"artifact_not_found"`
- Audit call: `AuditLogger(org.db).log_asset_put(…)` → `AuditLogger(org.db).log_artifact_put(…)`
- List-body key: `"assets": [...]` → `"artifacts": [...]`
- The `MAX_ASSET_BYTES` constant reference becomes `MAX_ARTIFACT_BYTES`

- [ ] **Step 3: Update app.py + routes/orgs.py**

In `src/daemon/app.py`:
- Line 11: `from src.daemon.routes import (…, assets, …)` → `… artifacts …`
- Line 98 (lifespan): `OrgPaths(org.root).assets_dir.mkdir(exist_ok=True)` → `OrgPaths(org.root).artifacts_dir.mkdir(exist_ok=True)`
- Line 162: `app.include_router(assets.router, prefix=…, tags=["assets"])` → `app.include_router(artifacts.router, prefix=…, tags=["artifacts"])`

In `src/daemon/routes/orgs.py:54`:
- `OrgPaths(org_root).assets_dir.mkdir(exist_ok=True)` → `OrgPaths(org_root).artifacts_dir.mkdir(exist_ok=True)`

- [ ] **Step 4: Defer commit — covered by Task 2.3 (path helper) + Task 2.4 (audit logger)**

---

### Task 2.3: Rename `OrgPaths.assets_dir` → `artifacts_dir`

**Files:**
- Modify: `src/orchestrator/_paths.py:40-42`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Update test_paths.py**

```bash
grep -n 'assets' tests/test_paths.py
```

Rename `assets_dir` references to `artifacts_dir`; rename any test function names accordingly.

- [ ] **Step 2: Rename the property**

In `src/orchestrator/_paths.py`, replace lines 40-42 with:

```python
    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"
```

- [ ] **Step 3: Run path tests**

```bash
uv run pytest tests/test_paths.py -v
```

Expected: PASS.

---

### Task 2.4: Rename audit logger method + action + task_id prefix

**Files:**
- Modify: `src/infrastructure/audit_logger.py:491-497`
- Modify: `src/models.py:268` (docstring comment — the `asset_put` mention deferred from Task 1.1)
- Test: `tests/test_audit_logger.py`

- [ ] **Step 1: Update test_audit_logger.py in lockstep**

```bash
grep -n 'asset' tests/test_audit_logger.py
```

For each hit:
- Test function names like `test_log_asset_put_*` → `test_log_artifact_put_*`
- Method call `log_asset_put(...)` → `log_artifact_put(...)`
- Expected `action` value `"asset_put"` → `"artifact_put"`
- Expected `task_id` value `f"asset:{name}"` → `f"artifact:{name}"`

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_audit_logger.py -v
```

Expected: FAIL (the renamed method doesn't exist yet).

- [ ] **Step 3: Rename in audit_logger.py**

Replace lines 491-497 with:

```python
    def log_artifact_put(self, name: str, size_bytes: int, agent: str) -> None:
        self._db.insert_audit_log(
            task_id=f"artifact:{name}",  # namespaced to avoid collision with TASK-/TALK-/SR- ids in get_audit_logs(task_id)
            agent=agent,
            action="artifact_put",
            payload={"name": name, "size_bytes": size_bytes},
        )
```

- [ ] **Step 4: Update the docstring comment in models.py:268**

```bash
grep -n 'asset_put\|asset:' src/models.py
```

Rewrite the comment to reference `artifact_put` / `artifact:<name>` instead.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_audit_logger.py tests/test_models.py -v
```

Expected: PASS.

---

### Task 2.5: Rename CLI subcommand + client methods

**Files:**
- Modify: `src/cli.py` (lines 1713-1750, 2882-2901)
- Modify: `src/client/client.py` (lines 105-148)
- Rename: `tests/test_cli_assets.py` → `tests/test_cli_artifacts.py` (git mv)

- [ ] **Step 1: git mv the test file**

```bash
git mv tests/test_cli_assets.py tests/test_cli_artifacts.py
```

- [ ] **Step 2: Rewrite the renamed test file**

In `tests/test_cli_artifacts.py`, replace all occurrences (use `replace_all` in Edit):
- `assets` → `artifacts`
- `Asset` → `Artifact`
- `asset_` → `artifact_`
- Function names like `test_assets_put_*` → `test_artifacts_put_*`
- Mocked client method names `put_asset`, `list_assets`, `get_asset` → `put_artifact`, `list_artifacts`, `get_artifact`
- HTTP paths `/api/v1/orgs/{slug}/assets` → `/api/v1/orgs/{slug}/artifacts`
- Subcommand strings `["assets", "put"]` → `["artifacts", "put"]` (etc.)

After replace_all, re-read the file to verify.

- [ ] **Step 3: Rename in src/client/client.py**

- `put_asset` → `put_artifact`, URL `/api/v1/orgs/{slug}/assets` → `/api/v1/orgs/{slug}/artifacts`
- `list_assets` → `list_artifacts`, URL same change; response body parses `"artifacts"` key now
- `get_asset` → `get_artifact`, URL `/assets/{name}` → `/artifacts/{name}`

- [ ] **Step 4: Rename in src/cli.py**

- Lines 1713-1750 (handler functions):
  - `cmd_assets_put` → `cmd_artifacts_put`
  - `cmd_assets_list` → `cmd_artifacts_list`
  - `cmd_assets_get` → `cmd_artifacts_get`
  - All internal `client.put_asset` / `client.list_assets` / `client.get_asset` → `put_artifact` / `list_artifacts` / `get_artifact`
  - `body["assets"]` → `body["artifacts"]` and `assets = body[...]` → `artifacts = body[...]`
  - Error print "no assets" → "no artifacts"
- Lines 2882-2901 (argparse setup):
  - Parser var `p_assets` → `p_artifacts`
  - `p_artifacts = sub.add_parser("artifacts", help="…")` (was "assets")
  - Subparser var `assets_sub` → `artifacts_sub`
  - Subparser names `p_assets_put`, `p_assets_list`, `p_assets_get` → `p_artifacts_put`, `p_artifacts_list`, `p_artifacts_get`
  - Handler bindings `set_defaults(func=cmd_artifacts_put)` etc.

Update help strings: "Manage org-wide shared assets" → "Manage org-wide shared artifacts" (preserve exact wording style; check current value).

- [ ] **Step 5: Run CLI tests**

```bash
uv run pytest tests/test_cli_artifacts.py tests/test_cli.py -v
```

Expected: PASS.

---

### Task 2.6: Rename workspace adapter prompt section

**Files:**
- Modify: `src/orchestrator/workspace_adapters.py` (lines 138-161, 581)
- Test: `tests/test_workspace_adapters.py`

- [ ] **Step 1: Update test_workspace_adapters.py**

Rename `_shared_assets_section` → `_shared_artifacts_section` in test references, and any expected-string assertions.

- [ ] **Step 2: Rename function + body**

In `src/orchestrator/workspace_adapters.py`, lines 138-161:

```python
def _shared_artifacts_section() -> list[str]:
    return [
        "## Shared Artifacts (org-wide)\n",
        "Path: `<runtime>/orgs/<slug>/artifacts/`. Drop persistent files your work",
        "produces — generated reports, exports, screenshots, PDFs, images. Files",
        "here survive across tasks and are visible to every agent in this org.\n",
        "Use cases: a generated PDF report another agent needs to attach to a",
        "customer reply; a CSV export the founder will want to review; a screenshot",
        "captured during QA that the bug-triage agent should see.\n",
        "**Not** the KB. KB is for durable cross-agent *knowledge* (rules,",
        "references, founder rulings). Artifacts are for *files and binary blobs*.",
        "Don't put scratch work here — use your workspace `repos/`, learning",
        "entries, or task output for transient state.\n",
        "All access is via `happyranch`. Direct filesystem reads/writes won't work",
        "uniformly across executors — use the CLI:\n",
        "```",
        "happyranch artifacts put <local-path> --agent <you> [--name <name>]",
        "happyranch artifacts list",
        "happyranch artifacts get <name> --output <local-path>",
        "```\n",
        "Naming convention: prefix with your agent name + ISO date for",
        "traceability, e.g. `dev_agent-YYYY-MM-DD-perf-report.pdf`. Names must",
        "match `[A-Za-z0-9._-]+`; max 200 chars total. Per-file size cap: 10 MB.\n",
        "Names may use '/' as a path separator for logical folders;",
        "traversal-guard rejects '..', leading/trailing '/', empty '//',",
        "backslashes, absolute paths, and symlink escapes.\n",
    ]
```

- [ ] **Step 2b: Update the call site at line 581**

`*_shared_assets_section()` → `*_shared_artifacts_section()`

- [ ] **Step 3: Run adapter tests**

```bash
uv run pytest tests/test_workspace_adapters.py -v
```

Expected: PASS.

---

### Task 2.7: Rename remaining test files + sweep all `asset` references

**Files:**
- Rename: `tests/test_asset_store.py` → `tests/test_artifact_store.py` (git mv)
- Rename: `tests/daemon/test_assets_routes.py` → `tests/daemon/test_artifacts_routes.py` (git mv)
- Rename: `tests/integration/test_assets_e2e.py` → `tests/integration/test_artifacts_e2e.py` (git mv)
- Modify: `tests/integration/conftest.py` (any `assets` references in fixtures)
- Modify: `tests/test_paths.py` (already done in 2.3 — verify)

- [ ] **Step 1: git mv the three test files**

```bash
git mv tests/test_asset_store.py tests/test_artifact_store.py
git mv tests/daemon/test_assets_routes.py tests/daemon/test_artifacts_routes.py
git mv tests/integration/test_assets_e2e.py tests/integration/test_artifacts_e2e.py
```

- [ ] **Step 2: Rewrite each renamed test file**

In each file, use Edit's `replace_all` to swap (case-preserving — do them one at a time, biggest-string first):
- `AssetStore` → `ArtifactStore`
- `AssetInfo` → `ArtifactInfo`
- `InvalidAssetName` → `InvalidArtifactName`
- `AssetTooLarge` → `ArtifactTooLarge`
- `AssetNotFound` → `ArtifactNotFound`
- `MAX_ASSET_BYTES` → `MAX_ARTIFACT_BYTES`
- `asset_store` → `artifact_store` (import paths)
- `routes.assets` → `routes.artifacts`
- `log_asset_put` → `log_artifact_put`
- `asset_put` → `artifact_put`
- `asset_too_large` → `artifact_too_large`
- `invalid_asset_name` → `invalid_artifact_name`
- `asset_not_found` → `artifact_not_found`
- `/assets` → `/artifacts` (URL path segments)
- `assets_dir` → `artifacts_dir`
- `"assets"` → `"artifacts"` (response body keys)
- `f"asset:{name}"` → `f"artifact:{name}"`
- Test function names `test_asset_*` / `test_assets_*` → `test_artifact_*` / `test_artifacts_*`
- Prose comments mentioning "asset" → "artifact"

Re-read each file after to verify.

- [ ] **Step 3: Sweep integration/conftest.py**

```bash
grep -n 'asset' tests/integration/conftest.py
```

For any hit, rename to artifact analogue.

- [ ] **Step 4: Final grep for stragglers**

```bash
grep -rIn -e 'asset' -e 'Asset' --include='*.py' src/ tests/ 2>/dev/null | grep -v 'web/dist' | grep -v 'fake_claude'
```

Expected: empty (or only false positives like words containing "asset" in some unrelated string). Investigate and fix every real hit.

- [ ] **Step 5: Run the full unit suite**

```bash
uv run pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 6: Commit Phase 2 code**

```bash
git add -A
git commit -m "refactor: rename assets feature → artifacts (storage module, route, CLI, audit, prompts, tests)"
```

---

## Phase 3 — Migration sweep

### Task 3.1: Author the SQL migration

**Files:**
- Create: `scripts/migrations/2026-06-01_rename_assets_to_artifacts.sql`

- [ ] **Step 1: Write the SQL**

```sql
-- One-shot sweep for the 2026-06-01 assets→artifacts + per-agent artifacts→output rename.
-- Run once per per-org SQLite DB at <runtime>/orgs/<slug>/happyranch.db.
-- Safe to re-run (idempotent: column-rename will no-op via the existence check
-- on the second run; UPDATEs on already-rewritten paths match nothing).
--
-- Stop the daemon before running: `scripts/daemon.sh stop`

BEGIN;

-- 1. Rename per-agent artifact-output columns on tasks + task_results.
--    SQLite 3.25+ supports RENAME COLUMN; macOS ships 3.39+.
ALTER TABLE tasks         RENAME COLUMN final_artifact_dir TO final_output_dir;
ALTER TABLE task_results  RENAME COLUMN artifact_dir       TO output_dir;

-- 2. Rewrite stored relative path strings that still start with 'artifacts/'
--    so they point at the renamed on-disk dir 'output/'. The shell sweep
--    will physically `mv` the workspace dir.
UPDATE tasks
   SET final_output_dir = 'output/' || substr(final_output_dir, length('artifacts/') + 1)
 WHERE final_output_dir LIKE 'artifacts/%';

UPDATE task_results
   SET output_dir = 'output/' || substr(output_dir, length('artifacts/') + 1)
 WHERE output_dir LIKE 'artifacts/%';

-- 3. Audit log: forward-only. No UPDATE on audit_log rows — old `asset_put`
--    actions and `asset:<name>` task_ids remain as historical record.

COMMIT;

-- Verification queries (read-only).
SELECT 'tasks columns (must include final_output_dir, NOT final_artifact_dir):' AS check_name;
SELECT name FROM pragma_table_info('tasks') WHERE name LIKE '%output%' OR name LIKE '%artifact%';

SELECT 'task_results columns (must include output_dir, NOT artifact_dir):' AS check_name;
SELECT name FROM pragma_table_info('task_results') WHERE name LIKE '%output%' OR name LIKE '%artifact%';

SELECT 'task_results.output_dir still starting with artifacts/ (should be 0):' AS check_name;
SELECT COUNT(*) FROM task_results WHERE output_dir LIKE 'artifacts/%';

SELECT 'tasks.final_output_dir still starting with artifacts/ (should be 0):' AS check_name;
SELECT COUNT(*) FROM tasks WHERE final_output_dir LIKE 'artifacts/%';
```

- [ ] **Step 2: Commit**

```bash
git add scripts/migrations/2026-06-01_rename_assets_to_artifacts.sql
git commit -m "feat(migrations): SQL for assets→artifacts + per-agent artifacts→output rename"
```

---

### Task 3.2: Author the shell sweep

**Files:**
- Create: `scripts/migrations/2026-06-01_rename_assets_to_artifacts.sh`

- [ ] **Step 1: Write the shell script**

```bash
#!/usr/bin/env bash
# Run the 2026-06-01 assets→artifacts + per-agent artifacts→output rename
# against every per-org SQLite DB + filesystem dir in the active runtime
# container.
#
# Usage:
#   scripts/migrations/2026-06-01_rename_assets_to_artifacts.sh [<runtime-dir>]
#
# Defaults to ~/.local/share/happyranch-runtime when no path is given.
# STOP THE DAEMON FIRST: scripts/daemon.sh stop
set -euo pipefail

RUNTIME="${1:-$HOME/.local/share/happyranch-runtime}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="$SCRIPT_DIR/2026-06-01_rename_assets_to_artifacts.sql"

if [[ ! -d "$RUNTIME/orgs" ]]; then
    echo "no orgs directory at $RUNTIME/orgs" >&2
    exit 1
fi

shopt -s nullglob
ORG_DIRS=("$RUNTIME"/orgs/*/)
if [[ ${#ORG_DIRS[@]} -eq 0 ]]; then
    echo "no per-org dirs under $RUNTIME/orgs" >&2
    exit 1
fi

for org_dir in "${ORG_DIRS[@]}"; do
    org_dir="${org_dir%/}"
    echo "=== org: $org_dir ==="

    # 1. Filesystem: rename org-shared store assets/ → artifacts/.
    if [[ -d "$org_dir/assets" && ! -e "$org_dir/artifacts" ]]; then
        mv "$org_dir/assets" "$org_dir/artifacts"
        echo "  moved assets/ → artifacts/"
    elif [[ -d "$org_dir/artifacts" && ! -d "$org_dir/assets" ]]; then
        echo "  skipped (assets/ already renamed to artifacts/)"
    elif [[ -d "$org_dir/assets" && -d "$org_dir/artifacts" ]]; then
        echo "  WARNING: both assets/ and artifacts/ exist — manual resolution required" >&2
    fi

    # 2. Filesystem: rename per-agent workspaces/<agent>/artifacts → output.
    if [[ -d "$org_dir/workspaces" ]]; then
        for ws in "$org_dir"/workspaces/*/; do
            ws="${ws%/}"
            if [[ -d "$ws/artifacts" && ! -e "$ws/output" ]]; then
                mv "$ws/artifacts" "$ws/output"
                echo "  moved $(basename "$ws")/artifacts → output"
            elif [[ -d "$ws/artifacts" && -d "$ws/output" ]]; then
                echo "  WARNING: $(basename "$ws") has both artifacts/ and output/ — manual resolution required" >&2
            fi
        done
    fi

    # 3. SQL: rename columns + rewrite stored path strings.
    db="$org_dir/happyranch.db"
    if [[ -f "$db" ]]; then
        echo "  sweeping $db"
        sqlite3 "$db" < "$SQL_FILE"
    else
        echo "  no DB at $db, skipping SQL" >&2
    fi
    echo
done

echo "done. Restart the daemon: scripts/daemon.sh start"
```

- [ ] **Step 2: Make executable + commit**

```bash
chmod +x scripts/migrations/2026-06-01_rename_assets_to_artifacts.sh
git add scripts/migrations/2026-06-01_rename_assets_to_artifacts.sh
git commit -m "feat(migrations): shell sweep for assets→artifacts + per-agent artifacts→output rename"
```

---

### Task 3.3: Idempotency dry-run

- [ ] **Step 1: Create a throwaway fixture runtime**

```bash
mkdir -p /tmp/hr-migration-test/orgs/sample/{assets,workspaces/dev_agent/artifacts/TASK-1}
echo 'hello' > /tmp/hr-migration-test/orgs/sample/assets/foo.txt
echo 'output' > /tmp/hr-migration-test/orgs/sample/workspaces/dev_agent/artifacts/TASK-1/note.md
# Create a minimal SQLite DB with the old column shape so SQL exercises both branches.
sqlite3 /tmp/hr-migration-test/orgs/sample/happyranch.db <<'SQL'
CREATE TABLE tasks (id TEXT PRIMARY KEY, final_artifact_dir TEXT);
CREATE TABLE task_results (id INTEGER PRIMARY KEY, artifact_dir TEXT);
INSERT INTO tasks VALUES ('T-1', 'artifacts/T-1');
INSERT INTO task_results (artifact_dir) VALUES ('artifacts/T-1');
SQL
```

- [ ] **Step 2: Run the sweep**

```bash
scripts/migrations/2026-06-01_rename_assets_to_artifacts.sh /tmp/hr-migration-test
```

Expected output: moves logged, SQL verification queries return the renamed columns + 0 stale path counts.

- [ ] **Step 3: Re-run to confirm idempotency**

```bash
scripts/migrations/2026-06-01_rename_assets_to_artifacts.sh /tmp/hr-migration-test
```

Expected: "skipped (assets/ already renamed)" messages; SQL ALTERs would error on the second run (column doesn't exist). Adjust: if SQL re-run errors, wrap the ALTER statements with a SQLite-safe check, OR document that the shell wrapper should detect a "column already renamed" state and skip the SQL. Simplest fix: in the shell, before invoking sqlite3, probe `sqlite3 "$db" "SELECT name FROM pragma_table_info('tasks') WHERE name='final_artifact_dir';"` — if empty, skip the SQL.

Update the shell script with that probe and re-test.

- [ ] **Step 4: Cleanup + commit if the shell needed an idempotency fix**

```bash
rm -rf /tmp/hr-migration-test
# If shell was updated:
git add scripts/migrations/2026-06-01_rename_assets_to_artifacts.sh
git commit -m "fix(migrations): probe-then-run so the sweep is idempotent"
```

---

## Phase 4 — Docs, snapshot, web UI

### Task 4.1: Regenerate OpenAPI snapshot

- [ ] **Step 1: Regen**

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py
```

- [ ] **Step 2: Sanity-check the diff**

```bash
git diff tests/contract/openapi.json | head -100
```

Expected: `/api/v1/orgs/{slug}/assets` paths removed; `/api/v1/orgs/{slug}/artifacts` paths added. Recall endpoint's `include_artifact` query param renamed to `include_output`. Recall body's `artifact` key (if it's in the response schema) renamed to `output`.

- [ ] **Step 3: Run the snapshot test in non-regen mode to confirm green**

```bash
uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Expected: PASS.

---

### Task 4.2: Update web `EXCLUDED_PATHS`

**Files:**
- Modify: `web/src/test/openapi-coverage.test.ts` (lines 147-149)

- [ ] **Step 1: Update the three excluded paths**

In `web/src/test/openapi-coverage.test.ts`, replace:

```ts
'POST /api/v1/orgs/{slug}/assets',
'GET /api/v1/orgs/{slug}/assets',
'GET /api/v1/orgs/{slug}/assets/{name}',
```

with:

```ts
'POST /api/v1/orgs/{slug}/artifacts',
'GET /api/v1/orgs/{slug}/artifacts',
'GET /api/v1/orgs/{slug}/artifacts/{name}',
```

Preserve the surrounding justification comment but update its wording ("agent-facing v1 — CLI only, founder UI deferred" stays accurate; just swap the noun).

- [ ] **Step 2: Run the coverage test**

```bash
cd web && npm test -- openapi-coverage
```

Expected: PASS.

---

### Task 4.3: Update top-level docs

**Files:**
- Modify: `CLAUDE.md` ("## Shared Assets (org-wide blob store)" section)
- Modify: `README.md` (line ~138 CLI reference)
- Modify: `skills/happyranch/SKILL.md` (lines 72, 75, 149-162, 335)

- [ ] **Step 1: CLAUDE.md**

Replace the section header `## Shared Assets (org-wide blob store)` with `## Shared Artifacts (org-wide blob store)`. Update all body text:
- `<runtime>/orgs/<slug>/assets/` → `<runtime>/orgs/<slug>/artifacts/`
- `src/infrastructure/asset_store.py` → `src/infrastructure/artifact_store.py`
- `src/daemon/routes/assets.py` → `src/daemon/routes/artifacts.py`
- `happyranch assets {put,list,get}` → `happyranch artifacts {put,list,get}`
- The audit-overload section: `asset_put` → `artifact_put`, `f"asset:{name}"` → `f"artifact:{name}"`
- The "Not the KB" note: "assets are blobs" → "artifacts are blobs"
- "Dir created at fresh-org init AND idempotently at lifespan startup" — wording stays; just the noun.

Also update CLAUDE.md's line 199 (Chain prior-leg context) — `artifact_dir` reference → `output_dir`. Line 589 example recall — `--tree` / `--fetch-artifact` → `--fetch-output`; "files under `artifacts/<task_id>/`" → "files under `output/<task_id>/`".

Skim the rest of CLAUDE.md (`grep -n 'artifact\|asset' CLAUDE.md`) for any miss; update each hit per the rename.

- [ ] **Step 2: README.md**

```bash
grep -n 'assets\|asset_\|Asset' README.md
```

Update each hit. The line ~138 CLI reference is the most visible one: `happyranch assets …` → `happyranch artifacts …`.

- [ ] **Step 3: skills/happyranch/SKILL.md**

Search and update:
- Lines 72, 75, 335: `--fetch-artifact` → `--fetch-output`, "artifacts" in prose → "output files" where it refers to the per-agent dir
- Lines 149-162: the full `assets` command block becomes `artifacts`. Update subcommand names, prose, examples.
- Sweep grep: `grep -n 'asset\|artifact' skills/happyranch/SKILL.md` — fix every hit consistent with the rename.

- [ ] **Step 4: Update protocol/06-knowledge-base.md**

Line 36 currently distinguishes assets (org-wide) from per-task artifacts. After rename it becomes: artifacts are the org-wide blob store; per-task output goes under `output/<task_id>/`. Rewrite the line accordingly.

- [ ] **Step 5: Commit docs**

```bash
git add CLAUDE.md README.md skills/happyranch/SKILL.md protocol/06-knowledge-base.md
git commit -m "docs: rename assets→artifacts feature and per-agent artifacts→output convention across CLAUDE.md / README / skills / protocol"
```

---

### Task 4.4: Spec / plan housekeeping (light)

We do NOT rewrite historical specs — they're snapshots of past state. We DO add a pointer note to the old shared-assets plan so future readers know about the rename.

**Files:**
- Modify: `docs/superpowers/plans/2026-05-27-shared-assets.md` (top of file)

- [ ] **Step 1: Add a deprecation note**

At the top of `docs/superpowers/plans/2026-05-27-shared-assets.md`, immediately under the `# Shared Assets Implementation Plan` header, add:

```markdown
> **2026-06-01 note:** This feature was renamed to "artifacts" — see
> `docs/superpowers/plans/2026-06-01-rename-assets-to-artifacts.md`. The
> design below is otherwise current; substitute `assets` → `artifacts`,
> `AssetStore` → `ArtifactStore`, `asset_put` → `artifact_put` when reading.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-05-27-shared-assets.md
git commit -m "docs(plans): pointer from shared-assets plan to the artifacts rename plan"
```

---

## Phase 5 — Verification

### Task 5.1: Full unit + integration test pass

- [ ] **Step 1: Unit tests**

```bash
uv run pytest tests/ -v
```

Expected: PASS. Any failure should be a stragger reference we missed — fix and commit.

- [ ] **Step 2: Integration tests**

```bash
uv run pytest tests/ -v -m integration
```

Expected: PASS. These spawn a real daemon + fake CLI, exercising routes + filesystem.

- [ ] **Step 3: Final grep audit**

```bash
grep -rIn -e '\bassets\b' -e '\bAsset[A-Z]' -e 'asset_put' -e 'asset:' -e 'log_asset_put' \
  --include='*.py' --include='*.ts' --include='*.tsx' --include='*.md' --include='*.sh' \
  src/ tests/ web/src/ skills/ protocol/ CLAUDE.md README.md docs/superpowers/plans/2026-06-01-rename-assets-to-artifacts.md scripts/ 2>/dev/null \
  | grep -v 'web/dist' \
  | grep -v '2026-05-27-shared-assets.md' \
  | grep -v '2026-06-01-rename-assets-to-artifacts.md'
```

Expected: empty.

```bash
grep -rIn -e 'artifact_dir' -e 'final_artifact_dir' -e '--artifact-dir' -e '--fetch-artifact' -e 'include_artifact' \
  --include='*.py' --include='*.ts' --include='*.tsx' --include='*.md' --include='*.sh' \
  src/ tests/ web/src/ skills/ protocol/ CLAUDE.md README.md scripts/ 2>/dev/null \
  | grep -v 'web/dist'
```

Expected: empty (or only in the migration SQL/shell where the old names are intentionally referenced for column-rename).

If either grep returns hits, fix and commit.

- [ ] **Step 4: Smoke-test the daemon + CLI end-to-end**

```bash
scripts/daemon.sh start
happyranch artifacts list   # should not error; expect "no artifacts"
echo 'hello' > /tmp/artifact.txt
happyranch artifacts put /tmp/artifact.txt --agent dev_agent --name dev_agent-2026-06-01-smoke.txt
happyranch artifacts list
happyranch artifacts get dev_agent-2026-06-01-smoke.txt --output /tmp/artifact-read.txt
diff /tmp/artifact.txt /tmp/artifact-read.txt
scripts/daemon.sh stop
```

Expected: full round-trip works; `diff` is empty.

- [ ] **Step 5: Final commit if anything was touched in Step 3**

```bash
git status
# If clean, no commit needed.
```

---

## Self-Review Checklist (filled in)

1. **Spec coverage:**
   - `assets` → `artifacts`: storage module ✓, route ✓, app.py registration ✓, orgs.py lifespan mkdir ✓, `OrgPaths.assets_dir` ✓, audit method + action + task_id ✓, error codes ✓, constants ✓, CLI subcommand + handlers + parser ✓, client methods ✓, workspace adapter prompt ✓, all 4 dedicated test files ✓, OpenAPI snapshot ✓, web EXCLUDED_PATHS ✓, CLAUDE.md ✓, README ✓, skills SKILL.md ✓, protocol/06-knowledge-base.md ✓, shared-assets plan pointer ✓.
   - Per-agent `artifacts/` → `output/`: Pydantic fields ✓, DB schema (tasks + task_results columns) ✓, idempotent ALTER list ✓, row mappers ✓, `insert_task_result` signature + INSERT ✓, `_complete` + `run_step` ✓, chain context label + key ✓, orchestrator task-summary label ✓, thread_store ✓, `_read_artifact` + `MAX_ARTIFACT_BYTES` + `include_artifact` + recall body key ✓, CLI `--artifact-dir` + `--fetch-artifact` + payload key ✓, client recall method ✓, agent skill convention (start-task + thread + 00-completion-contract) ✓, workspace_adapters recall hint ✓.
   - Migration: SQL column rename + path-string rewrite ✓, shell sweep filesystem move + per-org DB loop ✓, idempotency probe ✓.
2. **Placeholder scan:** Every step has the actual code or command. No "TBD", no "handle edge cases", no "similar to Task N".
3. **Type consistency:** `output_dir` (Pydantic + DB + CLI), `final_output_dir` (Pydantic + DB), `ArtifactStore`/`ArtifactInfo`/`InvalidArtifactName`/`ArtifactTooLarge`/`ArtifactNotFound` (storage), `log_artifact_put` + `"artifact_put"` + `"artifact:{name}"` (audit), `MAX_ARTIFACT_BYTES` (storage), `MAX_OUTPUT_BYTES` (recall reader), `_read_output` (recall helper), `include_output` (query param). No drift across tasks.
