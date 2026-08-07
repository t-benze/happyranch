# Remove GitNexus Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the GitNexus mandate and local integration footprint from the HappyRanch repo, replacing the `gitnexus_impact`/`gitnexus_detect_changes` review gate with a tool-agnostic native evidence bundle (`git diff --stat` + targeted `rg` sweep + focused tests) so the surgical-change/blast-radius control survives without the dependency.

**Architecture:** This is a documentation/config removal, not a code change — GitNexus was never a runtime dependency, CI step, or package dependency in this repo. It exists only as (1) an auto-inserted section in `CLAUDE.md` bounded by `<!-- gitnexus:start -->` / `<!-- gitnexus:end -->` markers, (2) one prose reference in an active umbrella scope doc, (3) an untracked/gitignored local skill cache (`.claude/skills/gitnexus/`) and index cache (`.gitnexus/`), and (4) a dead `.gitignore` entry. Historical docs (plans, specs, a past design-overhaul audit, and a KB-fixture example in a test file) mention GitNexus as a record of what happened at the time and must not be rewritten.

**Tech Stack:** Markdown docs, `.gitignore`, shell (`rg`, `git`).

## Global Constraints

- Scope is **this repo only**. Do not touch the global GitNexus MCP server registration in `~/.claude.json` — confirmed with the user this stays, since it may serve other projects.
- Do not modify `docs/superpowers/plans/*.md`, `docs/superpowers/specs/*.md`, `docs/design-overhaul/*.md`, or the KB-fixture example in `tests/test_learnings_store.py` (lines ~348-353) — these are historical/audit-trail or test-fixture content, not live tooling instructions, per `CLAUDE.md`'s own append-only-history convention.
- Do not modify anything under `.worktrees/`, `.claude/worktrees/` — these are separate, possibly-active git worktrees for other tasks (`TASK-3893`, `system-assistant`, `TASK-3935`); they are out of scope and touching them risks other in-progress work.
- Preserve the intent of the removed gate: state blast radius before editing, verify the diff against that declared radius before committing, and escalate explicitly on high-risk changes.
- Final acceptance check: `rg -i gitnexus` over tracked files must return **only** the allowlisted historical/fixture paths listed above — nothing else.

---

### Task 1: Replace the GitNexus section in CLAUDE.md with a native evidence-bundle gate

**Files:**
- Modify: `CLAUDE.md:104-148`

**Interfaces:**
- Produces: the `## Change Impact Analysis` section other docs (Task 2) point to as "CLAUDE.md § Change Impact Analysis".

- [ ] **Step 1: Confirm the exact block to remove**

Run: `grep -n 'gitnexus:start\|gitnexus:end\|^## GitNexus' CLAUDE.md`
Expected output:
```
104:## GitNexus
106:<!-- gitnexus:start -->
148:<!-- gitnexus:end -->
```
This confirms lines 104-148 are the complete, self-contained block (header + marker-delimited body) to replace.

- [ ] **Step 2: Replace lines 104-148**

Use the Edit tool with `old_string` set to the full text from `## GitNexus` (line 104) through `<!-- gitnexus:end -->` (line 148) inclusive, and `new_string`:

```markdown
## Change Impact Analysis

Before editing any function, class, or method: state the blast radius before making the edit — search for its call sites (`rg` for the symbol name, its imports, and its references) and name the direct callers, affected modules, and a risk level (LOW/MEDIUM/HIGH) to the user. Stop and escalate explicitly before proceeding if risk is HIGH.

Before committing: run `git diff --stat` plus a targeted `rg` sweep for the changed symbol/import/call-site names, and run focused tests covering the changed behavior (`uv run python -m pytest <path> -v` for Python, `npm run test` in `web/` for TS). This is the evidence bundle a reviewer checks against the blast radius stated in Step 1 — the diff should only touch what was declared.

Do not rename symbols with a blind find-and-replace across the repo — confirm all call sites with `rg` first. A rename that misses a dynamic reference (string-based dispatch, a config-driven agent name, etc.) fails silently at runtime instead of at compile time.
```

- [ ] **Step 3: Verify the file is well-formed**

Run: `grep -c '^## ' CLAUDE.md && grep -i gitnexus CLAUDE.md`
Expected: a section count (unchanged number of `##` headers, since one section was replaced 1:1) and **no output** from the `gitnexus` grep.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): replace GitNexus impact gate with native evidence-bundle gate"
```

---

### Task 2: Update the active thr061 scope doc to drop the GitNexus-specific instruction

**Files:**
- Modify: `docs/thr061-redesign-scope.md:43`

**Interfaces:**
- Consumes: "CLAUDE.md § Change Impact Analysis" (produced by Task 1) as the thing this line now points to.

- [ ] **Step 1: Locate the line**

Run: `grep -n GitNexus docs/thr061-redesign-scope.md`
Expected:
```
43:2. **Task-Revisit web write (G3)** — expose the **existing** revisit mechanism (`happyranch revisit` / run_step revisit path) via a guarded task-action route + FE Revisit affordance with confirmation UX (pattern like resolve-escalation). **Guardrail:** if it would require a NEW `TaskStatus` value or transition → STOP + ESCALATE (founder-gated, MEM-044). GitNexus impact on the Python route; route `detect_changes` verbatim to the checker (MEM-067).
```

- [ ] **Step 2: Replace the trailing sentence**

Use the Edit tool with:

`old_string`:
```
GitNexus impact on the Python route; route `detect_changes` verbatim to the checker (MEM-067).
```

`new_string`:
```
State blast radius on the Python route per CLAUDE.md § Change Impact Analysis (`git diff --stat` + targeted `rg` sweep + focused tests) and route the evidence bundle to the checker for review (supersedes the retired GitNexus gate referenced in MEM-067).
```

Do not touch `MEM-044` or the KB memory ID `MEM-067` itself — those are references into the running org's own KB store outside this repo, not files this plan can edit.

- [ ] **Step 3: Verify**

Run: `grep -in gitnexus docs/thr061-redesign-scope.md`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add docs/thr061-redesign-scope.md
git commit -m "docs(thr061): point Task-Revisit guardrail at the native impact-analysis gate"
```

---

### Task 3: Drop the dead `.gitnexus` entry from `.gitignore`

**Files:**
- Modify: `.gitignore:53`

- [ ] **Step 1: Confirm the line**

Run: `grep -n gitnexus .gitignore`
Expected: `53:.gitnexus`

- [ ] **Step 2: Remove it**

Use the Edit tool to delete the `.gitnexus` line from `.gitignore` (remove the line entirely, not just blank it — check the surrounding lines first so the diff doesn't leave a stray blank line inconsistent with the rest of the file's style).

- [ ] **Step 3: Verify**

Run: `grep -i gitnexus .gitignore; echo "exit=$?"`
Expected: `exit=1` (grep found nothing).

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: drop dead .gitnexus gitignore entry"
```

---

### Task 4: Clean up local untracked GitNexus artifacts in this checkout

**Files:**
- Delete (local disk only, not git-tracked): `.gitnexus/` (150MB index/cache dir), `.claude/skills/gitnexus/` (6 skill subdirs: `gitnexus-refactoring`, `gitnexus-debugging`, `gitnexus-exploring`, `gitnexus-guide`, `gitnexus-cli`, `gitnexus-impact-analysis`)

**Interfaces:**
- None — these paths are gitignored/untracked, so this step affects only the local working copy of this specific checkout, never `git status`.

- [ ] **Step 1: Confirm neither path is git-tracked before deleting**

Run: `git ls-files .gitnexus .claude/skills/gitnexus`
Expected: no output (confirms both are untracked; if this prints anything, STOP — that would mean something unexpected is tracked and needs investigation before deleting).

- [ ] **Step 2: Delete the local cache and skill tree**

```bash
rm -rf .gitnexus .claude/skills/gitnexus
```

Do NOT touch `.worktrees/TASK-3893/.gitnexus`, `.worktrees/system-assistant/.gitnexus`, `.claude/worktrees/TASK-3935/.gitnexus`, or their `.claude/skills/gitnexus` counterparts — those belong to other worktrees' checkouts and are out of scope per the Global Constraints.

- [ ] **Step 3: Verify**

Run: `ls .claude/skills/ 2>/dev/null; ls -d .gitnexus 2>/dev/null; echo done`
Expected: `.claude/skills/` is empty or absent, `.gitnexus` absent, no errors besides "No such file or directory" being fine.

- [ ] **Step 4: No commit** — nothing here is git-tracked, so there is nothing to stage or commit for this task.

---

### Task 5: Final sweep, test run, and acceptance check

**Files:** none (verification only)

- [ ] **Step 1: Repo-wide case-insensitive sweep**

Run:
```bash
rg -il -i 'gitnexus' --hidden -g '!.git' -g '!.worktrees' -g '!.claude/worktrees'
```
Expected output — **only** these allowlisted historical/fixture paths (no others):
```
docs/design-overhaul/engineering_manager-2026-06-16-design-overhaul-build-breakdown.md
docs/design-overhaul/engineering_manager-2026-06-16-design-overhaul-gap-analysis-validated.md
docs/superpowers/plans/2026-05-26-jobs.md
docs/superpowers/plans/2026-05-27-shared-assets.md
docs/superpowers/plans/2026-05-28-task-blocked-by-job.md
docs/superpowers/plans/2026-05-31-happyranch-rename.md
docs/superpowers/plans/2026-06-06-thread-escalation-surfacing.md
docs/superpowers/plans/2026-06-08-system-assistant.md
docs/superpowers/plans/2026-06-08-thread-talk-token-usage-scope.md
docs/superpowers/plans/2026-06-09-nightly-dreaming.md
docs/superpowers/plans/2026-06-09-thread-file-attachments.md
docs/superpowers/plans/2026-06-10-assistant-self-registration.md
docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md
docs/superpowers/specs/2026-05-31-happyranch-rename-design.md
docs/superpowers/specs/2026-06-10-working-hours-design.md
docs/superpowers/specs/2026-06-12-system-assistant-web-ui-design.md
docs/superpowers/specs/2026-07-24-unified-adapter-runtime-architecture.md
docs/superpowers/specs/2026-07-25-phase-0-executor-inventory.md
docs/superpowers/specs/2026-08-07-thr-107-slice-1a-impact-inventory.md
tests/test_learnings_store.py
```
(`CLAUDE.md`, `docs/thr061-redesign-scope.md`, and `.gitignore` must be **absent** from this list — that's what Tasks 1-3 fixed. This plan file itself, `docs/superpowers/plans/2026-08-08-remove-gitnexus.md`, will also legitimately match since it discusses the removal — that's expected and fine.)

- [ ] **Step 2: Confirm no MCP server config or dependency file in-repo references it**

Run: `find . -iname '.mcp.json' -not -path './.worktrees/*' -not -path './.claude/worktrees/*' -not -path './.git/*'; rg -i gitnexus package.json pyproject.toml web/package.json 2>/dev/null; echo done`
Expected: no `.mcp.json` found, no matches in the dependency files, `done` printed. (Already verified during inventory — this step re-confirms nothing regressed.)

- [ ] **Step 3: Run the unit test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: all tests pass, same pass count as before this plan started (this is a docs-only change; no test should newly fail or newly reference GitNexus behavior).

- [ ] **Step 4: Review the full diff**

Run: `git diff main --stat` (or `git log --oneline -5` plus `git diff HEAD~3 --stat` if commits from Tasks 1-3 already landed) and confirm only `CLAUDE.md`, `docs/thr061-redesign-scope.md`, `.gitignore`, and this plan file changed.

- [ ] **Step 5: Final commit (plan file itself)**

```bash
git add docs/superpowers/plans/2026-08-08-remove-gitnexus.md
git commit -m "docs: add GitNexus removal plan"
```
