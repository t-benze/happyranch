# PR CI Waiter / Guarded Merge — Design Spec

**Date:** 2026-06-30
**Status:** current
**Relates to:**
- `protocol/00-completion-contract.md` — PR CI wait and guarded merge path contract.
- `protocol/05c-orchestrator.md` — external waits use jobs, no new task state.
- `protocol/skills/jobs/SKILL.md` — PR CI / guarded merge helper subsection.
- `docs/agent-guides/orchestrator-contracts.md` — PR CI Wait / Guarded Merge developer guidance.
- `docs/agent-guides/features-and-invariants.md` — feature map entry and traps.
- `docs/superpowers/specs/2026-05-26-jobs-design.md` — jobs subsystem design.
- `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md` — task-blocked-by-job design.
- KB: `pr-ci-wait-guarded-merge` — founder-approved THR-047 ruling.

**Framing:** This spec is the PR-CI INSTANCE of the general external-terminal-condition wait contract defined in `protocol/00-completion-contract.md` ("Completion blocked on an asynchronous external condition") and `protocol/05c-orchestrator.md` ("External waits"). The protocol layer documents the domain-agnostic mechanism (jobs + `waiting_on_job_ids` → blocked → resume → inspect-verdict); this spec documents the PR-CI-specific engine design, verdict vocabulary, and guard semantics.

## 1. Goal

PR-producing engineering tasks whose requested outcome is landing code are not complete at PR creation. Today an agent can open a PR, report `done`, and the orchestrator considers the task terminal — leaving CI, merge, and the actual code landing in a founder-run tail that has no structured tracking, no bounded timeout, and no guard against stale heads or incomplete checks.

This design adds a first-class PR CI wait-and-merge path using existing primitives — jobs, `blocked_on_job_ids`, the task resume path — with zero new task states, zero schema migrations, and zero permission-model broadening. The contract is ratified in `protocol/00-completion-contract.md` and the agent-guide surfaces; this spec documents the engine design, verdict vocabulary, guard semantics, and implementation breakdown.

## 2. Non-goals

Out of scope:

- New `TaskStatus` values. The existing `in_progress(blocked_on_job)` state covers the entire wait.
- Schema migration. No new columns, no column overloading. `blocked_on_job_ids` already exists on `tasks`.
- Auth or permission-model changes. The helper is audited and narrow; agents never get broad `gh pr merge` allow-rules.
- Multi-PR batching, PR queueing / merge queues, or branch-protection policy authoring. This is a single-PR bounded waiter.
- Daemon-side GitHub credentials management. The daemon's environment provides `GH_TOKEN` or equivalent.
- Web UI for PR CI status. The waiter is an agent-facing engine; rendering is deferred.

## 3. Contract summary

The process, as ratified in `protocol/00-completion-contract.md`:

1. The task owner opens a PR (or pushes a final commit) and captures the repository, PR number, and exact PR head SHA.
2. After review (`APPROVE`) and QA (`PASS`) verdicts are obtained (via the existing chain or manual path), the task owner submits a bounded PR CI / guarded merge job through the first-class helper.
3. The task owner reports `status="blocked"` with `waiting_on_job_ids=["JOB-NNN"]`.
4. The task parks in `in_progress(blocked_on_job)`. The existing all-terminal job predicate resumes the task when the job completes.
5. On resume, the task owner inspects the structured job verdict. `done` only if `verdict=merged`; anything else triggers revise/fail/escalate.

## 4. Engine breakdown

### 4.1 PR CI waiter engine (`runtime/daemon/pr_ci_waiter.py`)

A pure engine with a command-runner injection point — no network calls in the implementation, no `gh` calls in unit tests. Takes:

- `repo` — `owner/repo`
- `pr_number` — int
- `pinned_head_sha` — full 40-char SHA
- `expected_checks` — list of check run / status context names
- `settle_seconds` — how long to wait for checks to appear after submission
- `poll_interval_seconds` — seconds between polls
- `timeout_seconds` — total bounded wait ceiling

Each poll:

1. Fetch PR state (head SHA, open/closed, draft status).
2. If head SHA != pinned_head_sha → `stale_head` (terminal, no merge).
3. If PR closed → `pr_closed` (terminal, no merge).
4. If PR draft → `pr_draft` (terminal, no merge).
5. Collect check runs and commit statuses for the pinned SHA.
6. During settle window (elapsed < settle_seconds): if expected/required checks are absent, continue polling — do not treat absence as pass.
7. After settle window: if any expected check never appeared → `checks_missing` (terminal).
8. Classify each check: `queued` / `in_progress` / `pending` → non-terminal; `success` / `skipped` / `neutral` → passed; `failure` / `cancelled` / `timed_out` / `action_required` → failed.
9. If all expected checks passed → `ci_pass` (terminal, ready for merge).
10. If any expected check failed → `ci_failed` (terminal, no merge).
11. If elapsed >= timeout_seconds → `timeout` (terminal).
12. GitHub API errors → `github_error` (terminal, with error detail).

Exit code mapping: `ci_pass` = 0; all other verdicts = non-zero exit codes (distinct codes per verdict for scripting).

### 4.2 Guarded merge engine (`runtime/daemon/pr_ci_merge.py` or extension of the waiter)

Inputs add:

- `merge_method` — `merge`, `squash`, or `rebase`
- `review_evidence_task_id` — task whose verdict must be `APPROVE`
- `qa_evidence_task_id` — task whose verdict must be `PASS`

The merge guard is conjunctive. Before attempting merge, the engine re-verifies:

1. Review evidence: the referenced task's completion report carries `verdict=APPROVE`.
2. QA evidence: the referenced task's completion report carries `verdict=PASS`.
3. PR head SHA is still the pinned SHA (re-fetched at merge time — the waiter's `ci_pass` may have been minutes ago).
4. CI verdict is `PASS` for the pinned SHA (the waiter's output).
5. PR is open and not draft.
6. GitHub mergeability is `CLEAN`.

If ALL guards pass, the engine performs the merge using the configured method. Output is a structured final JSON:

```json
{
  "verdict": "merged",
  "merged_sha": "<merge commit SHA>",
  "merged_at": "<iso timestamp>",
  "pr_number": 245,
  "pinned_head_sha": "abc123..."
}
```

If any guard fails, the verdict is the specific failure (`stale_head`, `ci_failed`, `checks_missing`, `timeout`, `pr_closed`, `pr_draft`, `github_error`, `merge_guard_review`, `merge_guard_qa`, `merge_guard_mergeable`, `merge_failed`) and the exit code is non-zero. The task owner sees the structured verdict on resume and decides revise/fail/escalate accordingly.

### 4.3 Verdict vocabulary

| Verdict | Meaning | Terminal | Merge-eligible |
|---------|---------|----------|----------------|
| `ci_pass` | All expected checks passed for pinned SHA | Yes | Yes |
| `ci_failed` | One or more expected checks failed | Yes | No |
| `stale_head` | PR head SHA changed from pinned SHA | Yes | No |
| `checks_missing` | Expected checks never appeared after settle window | Yes | No |
| `timeout` | Total bounded wait exceeded | Yes | No |
| `pr_closed` | PR was closed before merge | Yes | No |
| `pr_draft` | PR is in draft state | Yes | No |
| `github_error` | GitHub API returned an unrecoverable error | Yes | No |
| `merged` | All guards passed, PR merged | Yes | N/A (final) |
| `merge_guard_review` | Review evidence verdict is not APPROVE | Yes | No |
| `merge_guard_qa` | QA evidence verdict is not PASS | Yes | No |
| `merge_guard_mergeable` | GitHub mergeability is not CLEAN | Yes | No |
| `merge_failed` | Merge command returned non-zero | Yes | No |

### 4.4 Daemon route + CLI helper (job creation)

> **⚠ CORRECTION (2026-07-01 — THR-047 rework, PR #4):**
>
> The dedicated daemon route `POST /api/v1/orgs/{slug}/pr-ci/complete` and the
> `happyranch pr-ci complete` CLI verb described below are **SUPERSEDED**.
> They were ABANDONED in the founder redesign (THR-047 msg 36) and were NEVER
> merged to main.
>
> **Replacement model (see §8 and `runtime/daemon/pr_ci_waiter.py` / `runtime/daemon/pr_ci_merge.py`):**
>
> - **CI-POLL job:** a `review_required=false` job submitted through the
>   EXISTING generic `POST /jobs/submit` route.  The job script invokes
>   `python -m runtime.daemon.pr_ci_waiter --repo ... --pr N --head-sha <sha> --expected-check ...`
>   to poll CI via `gh` and print a structured verdict JSON to stdout (exit
>   code 0 = ci_pass).  The task owner reports `status=blocked` with
>   `waiting_on_job_ids=["JOB-NNN"]`.  On resume, the task owner inspects the
>   job verdict.  The poll job performs **NO merge** — it is strictly a
>   CI-status poller.
>
> - **GUARDED-MERGE step:** triggered by the resumed task (not a separate job)
>   via a second CLI entrypoint
>   `python -m runtime.daemon.pr_ci_merge --org ... --repo ... --pr N --head-sha <sha> --merge-method squash --ci-verdict ci_pass --review-task-id TASK-xxx --qa-task-id TASK-yyy`.
>   This re-enforces all guards (review APPROVE + QA PASS + mergeable CLEAN +
>   unchanged head SHA + open/non-draft) before performing the merge.
>
> **No new daemon route, no new auth surface, no permission-model change.**
> The poll job is submitted through the existing generic jobs infrastructure;
> the merge step runs on the daemon authority path — baseline agents never
> get raw `gh pr merge` grants.
>
> `protocol/skills/jobs/SKILL.md` and `docs/agent-guides/` integration
> remains deferred to PR #5.
>
> ---

---

**Historical text (retained for context — the design below is SUPERSEDED):**

A narrow agent-callback route (`POST /api/v1/orgs/{slug}/pr-ci/complete`) accepts:

- `task_id`, `session_id` — auth binding (same pattern as jobs submit)
- `repo`, `pr`, `head_sha`
- `expected_checks` — list of check context names
- `review_task_id`, `qa_task_id` — evidence task ids
- `timeout_seconds`, `settle_seconds`, `merge_method`

The route:

1. Validates task/session auth like jobs submit.
2. Validates the review and QA evidence task ids exist, are in the current task lineage, and carry the expected verdicts (`APPROVE` and `PASS`).
3. Constructs a daemon-run script that invokes the waiter + merge engine with the supplied parameters.
4. Creates a `jobs` row with `review_required=false`, `persistent=false`, bounded by `max_runtime_seconds`.
5. Returns `{job_id: "JOB-NNN"}` so the task owner can report `status=blocked` with `waiting_on_job_ids`.

The CLI verb:

```bash
happyranch pr-ci complete \
  --task-id TASK-123 \
  --session-id <sid> \
  --repo owner/repo \
  --pr 245 \
  --head-sha abc123 \
  --expected-check "Python CI" \
  --expected-check "Web CI" \
  --review-task-id TASK-review \
  --qa-task-id TASK-qa \
  --timeout-seconds 3600 \
  --settle-seconds 120 \
  --merge-method squash
```

Agents never submit arbitrary script text through this helper — the engine script is daemon-generated from the validated parameters.

## 5. Prompt and skill integration

Agent guidance surfaces (`docs/agent-guides/`, `protocol/skills/jobs/SKILL.md`, workspace bootstrap docs) are updated to:

- Instruct task owners that PR creation is not completion.
- Show the submit-helper → report-blocked → resume → inspect-verdict flow.
- Provide examples of the structured verdict JSON and how to interpret each outcome.
- Exclude raw `gh pr merge` from worker allow-rules.

## 6. Implementation breakdown (5 PRs)

### PR 1: Protocol/spec text (DOCS-ONLY — this spec and its contract inserts)

Scope: `protocol/`, `docs/agent-guides/`, `docs/superpowers/specs/` text additions. No engine code, no schema migration, no auth change, no permission-model change.

### PR 2: Pure PR CI waiter engine

Add `runtime/daemon/pr_ci_waiter.py`. Pure command-runner-injected engine with no network calls in tests. Unit tests cover: pass, failed check, pending-to-pass, no-checks settle, stale head, timeout, draft PR, closed PR, missing expected check, GitHub error.

### PR 3: Guarded merge engine

Extend the waiter or add `runtime/daemon/pr_ci_merge.py`. Inputs include merge method and review/QA evidence ids. Unit tests for every guard; test that merge command is not called unless all guards pass; test stale head between CI pass and merge; test non-clean mergeability.

### PR 4: Daemon route + CLI helper

> **⚠ CORRECTION (2026-07-01 — THR-047 rework):** The daemon-route design
> below is SUPERSEDED.  The actual PR #4 (reworked as PR #262) delivers
> two separate CLI entrypoints — `runtime/daemon/pr_ci_waiter.py` (poll job)
> and `runtime/daemon/pr_ci_merge.py` (guarded merge) — each with a `__main__`
> block wired to real `gh`.  No new daemon route, no new `happyranch` verb.
> See the correction block at §4.4 and the final model at §8 for details.

---

**Historical text (retained for context):**

Add agent-callback route `POST /api/v1/orgs/{slug}/pr-ci/complete` and CLI command `happyranch pr-ci complete`. Route validates auth, evidence task verdicts, and creates a bounded `review_required=false` job. Tests for auth/session mismatch, missing expected checks, invalid inputs, review/QA verdict gating, and job creation with bounded runtime.

### PR 5: Prompt/skill integration and end-to-end workflow test

Update agent guidance surfaces so PR-producing work is not reported complete at PR creation. Add examples to the jobs skill. Integration test with fake GitHub command output covering happy path (ci_pass → merged) and failure path (stale_head, ci_failed) without merging.

### Merge criteria

Do not merge implementation PRs (2-5) until:

- code_reviewer verdict is `APPROVE`;
- qa_engineer verdict is `PASS`;
- GitHub CI is `PASS`;
- PR head SHA is unchanged since review/QA;
- GitHub mergeable status is `CLEAN`.

## 7. Traps

- SHA-pin every wait. If the PR head changes between CI pass and merge, stop with `stale_head`.
- "No checks" is not pass. Use the settle window for check-run appearance; fail with `checks_missing` if expected checks never materialize.
- `mergeable` / `mergeStateStatus` is distinct from CI pass — it is an additional conjunctive guard.
- `gh pr merge --auto` is not proof of safety when branch protection lacks required checks on `main`.
- Merge is allowed only through the guarded helper or a founder-reviewed job — never through broad worker `gh pr merge` allow-rules.
- The waiter engine must be pure (command-runner-injected) so all verdict paths are testable without GitHub network access.

## 8. SUPERSEDED: §4.4 Daemon route + CLI helper and §6 PR-4 (Founder redesign, THR-047 msg 36, 2026-07-01)

**The daemon-route approach described in §4.4 and the PR-4 bullet in §6 is SUPERSEDED.** The founder's redesign dissolves the dedicated `/pr-ci/complete` daemon route entirely. The replacement model separates polling and merging into two distinct mechanisms, neither of which requires a new daemon route:

### Poll job (pure CI-status poller)

The `wait_for_ci` engine is invoked as a plain job submitted through the **existing generic jobs path** (`happyranch jobs submit` / `POST /jobs/submit`, already token-gated, unchanged). A real-`gh`-backed CLI entrypoint (`python -m runtime.daemon.pr_ci_waiter --repo ... --pr N --head-sha <sha> --expected-check ...`) runs the waiter engine with real GitHub callables, prints the structured verdict JSON to stdout, and exits with the mapped code from `VERDICT_EXIT_CODES`. The job is submitted with `review_required=false` so it auto-runs. The job performs **NO merge** — it is strictly a CI-status poller.

### Merge triggered by the resumed task

On resume, the task inspects the blocked poll-job verdict. If `ci_pass`, the task owner triggers `guarded_merge` as a **short daemon-run step** via a second CLI entrypoint (`python -m runtime.daemon.pr_ci_merge --org ... --repo ... --pr N --head-sha <sha> --merge-method squash --ci-verdict ci_pass --review-task-id TASK-xxx --qa-task-id TASK-yyy`). This re-enforces all guards (review APPROVE + QA PASS + mergeable CLEAN + unchanged head SHA + open/non-draft) before performing the merge. The merge runs on the daemon-run / EM-authority path — agents never get raw `gh pr merge` grants.

### What was built (PR #4, reworked)

- `runtime/daemon/pr_ci_waiter.py`: added `__main__` entrypoint with real-`gh` adapters (`_gh_fetch_pr_state`, `_gh_fetch_checks`, `_RealClock`).
- `runtime/daemon/pr_ci_merge.py`: added `__main__` entrypoint with real-`gh` adapters (`_gh_fetch_pr_state`, `_gh_fetch_mergeable`, `_gh_perform_merge`) and recall-based verdict fetching (`_recall_fetch_verdict` via `happyranch recall`).
- Unit tests: `tests/daemon/test_pr_ci_waiter_gh.py`, `tests/daemon/test_pr_ci_merge_gh.py` (mock subprocess — NO network).
- The pure engine functions (`wait_for_ci`, `guarded_merge`) are **unchanged** — only additive `__main__` blocks were appended.
- **No new daemon route was created.** The abandoned route branch (PR #253, `task/TASK-1354`) is superseded and should not be referenced for guidance.
