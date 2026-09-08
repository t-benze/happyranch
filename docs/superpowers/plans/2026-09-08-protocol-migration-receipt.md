# Protocol retirement migration receipt

Baseline: `01d5ede5`. Implementation branch: `refactor/protocol-retirement`.
Historical source locations below refer to that commit, not active files.
This is a migration record, not a new policy specification.

## Change radius

Production edits: `runtime/config.py` source resolution and legacy-setting
validation; `runtime/skills/sources.py` and system-contract source metadata;
`workspace_adapters.py` skill resolution and guidance; `org_config.py` retired
document discovery; task/thread/wake/dream/schedule prompt builders and their
direct callers; `system_assistant.py` knowledge discovery; wheel includes.
Assets: ten skill packages and their guard, six retired Markdown files, current
guides/navigation. Tests: affected source fixtures, prompt builders, assistant,
materialization, and packaged-source regression coverage.

The source root has callers in every workspace bootstrap/materialization seam.
`protocol_doc_manifest` also transported repository refresh warnings; that data
is retained as `repo_refresh_note` through all five producers, including both
thread builders and eviction fallback. Active-policy and attachment inputs are
independent and retain their validation. Source-path metadata does not enter the
directory content hash: canonical identity is derived from relative member paths
and bytes. Changed skill bodies receive new content hashes by normal materialization.

Risk: medium for asset relocation and prompt transport; no edits to canonical
store algorithms, permissions, task transitions, authorization, or schema.
The legacy default `protocol_dir=protocol` remains an inert compatibility field;
non-default values fail configuration validation with migration guidance.
No live org settings, canonical packages, or ledgers are changed by this branch.

## Requirement dispositions

E = implemented boundary retained; A = editorial/advisory guidance retained in a
runtime skill; O = obsolete/duplicated implementation narrative removed;
G = existing enforcement gap, instruction retained rather than silently relaxed.
Evidence paths name existing behavior suites; execution results are recorded
separately. All rows are owned by this branch except explicit follow-up G1.

| Historical source / requirement | Boundary and evidence | Disposition |
| --- | --- | --- |
| 00: task/session/agent callback binding; stale and unknown sessions rejected | `runtime/daemon/routes/tasks.py`; `tests/daemon/test_completion_route_blocked_on_jobs.py`, completion route tests | E |
| 00: confidence and local-CI evidence shape | `runtime/models.py` (`LocalCiEvidence`), completion route; `tests/test_models.py` | E |
| 00: every pushed PR must supply truthful CI evidence | G1 below; shape validation does not prove PR creation or a CI run | G; retain `start-task` requirement |
| 00: single-line absolute-file callback usage | CLI callback parsers and executor permissions; `tests/test_cli.py`, `tests/test_workspace_adapters.py` | E for implemented parsing/permissions; skill teaches usage |
| 00: review/QA canonical merge verdict extraction, null fallback, conflicting evidence refusal | `runtime/daemon/pr_ci_merge.py`; `tests/daemon/test_pr_ci_merge.py`, `test_pr_ci_merge_gh.py` | E |
| 00: completion retry and lag must not consume another session's report | task routes, `run_step.py`; `tests/test_thr211_completion_status_lag.py` | E |
| 00: owner decisions versus subtask completions | `runtime/models.py`, `runtime/orchestrator/run_step.py`; run-step tests | E |
| 00: delegation scope and failed-child revisit linkage | `run_step.py`; chain/fanout/failure-recovery suites | E |
| 00: reviewer APPROVE gate and chain progression | `runtime/orchestrator/chain.py`, `run_step.py`; `tests/test_run_step_chain.py` | E |
| 00: attachments resolved through lineage, bounded uploads | task attachment routes/store and materializer; attachment suites | E |
| 00: fanout width, acknowledgement, per-branch scope, all-child join | `run_step.py`; fanout and permission-inheritance tests | E |
| 00: blocked-on-job validation and terminal-result resume | task completion route and `run_step.py`; `tests/daemon/test_completion_route_blocked_on_jobs.py` | E |
| 00: same-root authority and single-use continuation envelope | `runtime/orchestrator/authority.py`; authority hook/envelope suites | E |
| 00: summary quality, risks, reviewer focus, knowledge contribution | task skill instructions; accepted fields remain in models | A; no claim of semantic verification |
| 05 index: architecture navigation and Claude-only/six-page summary | existing project-layout guide, `web/src/routes.tsx` | O |
| 05 index: manager policy overview | active policy code and existing guide summary | O duplicate |
| 05b: executor/profile/model selection and model reset on real switch | executors, agent routes; model/executor tests | E |
| 05b: stdin transport and oversized argv refusal | `runtime/orchestrator/executors.py`; transport tests | E |
| 05b: thread transcript canonical; exact eviction signatures and full fallback | `thread_runner.py`, executors; `tests/test_thread_resume_parity.py` | E |
| 05b: reply claim/range/settlement, exchange catch-up, restart recovery | thread store/runner; `tests/test_thread_reply_exchange.py` and delivery suites | E |
| 05b: provider breaker eligibility, cooldown, one probe and continuity | thread breaker/store/scheduler; `tests/test_thread_reply_breaker.py`, `test_thread_breaker_scheduler.py` | E |
| 05b: adapter v1 identity/hash/approval/conformance; optional earned resume remains dormant | adapter contract/registry/executor; `tests/test_custom_adapter_registry.py`, `test_thread_resume_adapter_probe.py` | E |
| 05b: direct-connect ceremony, headless proof, callback identity and launch fence | custom CLI routes/services; direct-connect suites | E |
| 05b: custom skill validation, eligibility, logical purge and publication barrier | `runtime/skills/`, custom skill routes; custom-skill suites | E |
| 05b: canonical per-member hashes, both-root symlinks, union and fail-closed launch/retry | canonical store, workspace adapters, launch validators; canonical production/materialization suites | E |
| 05b: no same-UID isolation or automatic byte healing; manual recovery only | canonical store/recovery routes and negative tests | E; limitations retained in executor guide |
| 05b: repo provisioning versus per-launch fast-forward refresh | context builder/workspace adapters; `tests/test_thr103_repo_refresh.py` | E |
| 05b: attachment ancestry and collision-safe materialization | attachment store/materializer; attachment tests | E |
| 05b: admission before launch; finish/residue/publish/release; shutdown/cancel/retry | host supervisor/platform backends; producer containment and supervisor suites | E |
| 05b: Linux limits, honest sampled/unavailable counters, bounded receipts | platform policy/backends and host receipt store; platform suites | E |
| 05b: registered binary path and bundled CLI/PATH resolution | executor registry/startup environment; binary/PATH tests | E |
| 05b: canonical scratch env and report-only manifests | task scratch and launch seam; `tests/test_task_scratch.py` | E |
| 05b: never editable-install a task worktree into the serving runtime | worktree guard/skill; guard tests cover detection, not all shell actions | G1-related mandatory instruction retained; detection is not universal shell enforcement |
| 05b: private memory, provenance, permanent LRN aliases | memory store/routes; memory/learning tests | E |
| 05b: weekly per-agent cleanup, advisory measurement, atomic report thread, first-two reports | cleanup scheduler; `tests/test_workspace_cleanup_scheduler.py` | E |
| 05b: scratch reclamation dormant and assertions not independent authority | `task_scratch_reclamation.py`; `tests/test_task_scratch_reclamation.py` | E dormant; no activation |
| 05b: schedule owner/session, recurrence grammar, caps, stale occurrence and audit semantics | schedule service/store/scheduler; schedule suites | E |
| 05b: capacity staged writes, CAS/audit and restart-only application | daemon capacity route/config; capacity suites | E |
| 05b: generic remote job contextual models and dark enrollment persistence | `runtime/remote_jobs/`, `remote_job_schema.py`; remote job contract/migration suites | E dormant; no shipping runner |
| 05b: old charter/prompt paths, 2–3 concurrency, Support-only persistent sessions, example costs | current agent definitions, config, supervisor, routes | O |
| 05c: team discovery, org content, dynamic roles and routing | teams/agent definitions/org config; registry and routing tests | E |
| 05c: hard-coded four teams/PostgreSQL production and sample business limits | org-agnostic runtime uses SQLite; policy examples are not kernel defaults | O |
| 05c: claimed role-based pathname denial, universal auto-approval and automatic violation detection | actual executor-native capabilities differ; no such generic detector | O inaccurate claims; no new restrictions introduced |
| 05c: obsolete `escalate(...)`/`waiting_for_approval` example | completion decisions and `TaskStatus.ESCALATED` | O |
| 05c: immutable policy activation, session binding, self-evaluation, candidate pins | authority store/routes/hook; authority suites | E; old S1/S4 temporal contradictions removed |
| 05c: bounded autonomous continuation and followup replacement lineage | authority/run-step/thread dispatch; followup/envelope tests | E |
| 05c: seven task states, root-only escalation and bounded recovery | models/run-step/database; task/recovery tests | E |
| 05c: restart liveness, zombie results, lag consumption and cancellation | startup/reaper/session ownership; zombie/lag/containment suites | E |
| 05c: portability exhaustive classification, schedule quiescence and zombie reconcile only | `runtime/portability/`, portability routes; portability tests | E; no export/import |
| 05c: jobs admission, reviews, external waits and guarded merge | jobs routes/runner, PR waiter/merge; jobs and PR suites | E |
| 05c: mandatory use of jobs for all long shell commands | jobs implements the workflow but cannot observe every same-UID shell action | G1-related instruction retained in jobs skill; no universal enforcement claim |
| 05c: system-contract exposure and managed catalog eligibility/withdrawal | `runtime/skills/system_contracts.py`, exposure/resolver/materializer; skill suites | E |
| 05c: protocol Markdown index in session prompts | retired discovery implementation; refresh-note coverage retained separately | O deliberate removal |
| 05e: old dashboard pages, API paths, LAN hosting and read-only claim | current SPA routes/OpenAPI/connector | O |
| 05e: cache-only summary, background refresh, DB snapshots and lock instrumentation | dashboard projection/routes/locking; dashboard tests | E; concise web-guide explanation retained |
| 06: required KB metadata, slug identity and body bound | KB route/store; `tests/test_kb_store.py`, `tests/daemon/test_routes_kb.py` | E |
| 06: duplicate conflict and explicit sibling override | KB write/store; KB route/store suites | E |
| 06: server author/update stamps; deletion checks and confirmation | KB store/routes; `tests/daemon/test_kb_delete_team_managers.py` | E within existing bearer/caller model; no identity strengthening implied |
| 06: durability, shared relevance, preserve useful facts, supersede preferred | existing task/reflection skills | A editorial judgment; no mandatory semantic admission invented |
| skills/start-task: completion, jobs, worktree and KB usage | instruction asset retained; implemented boundaries above | E/A/G1 as above; dead SR workflow replaced |
| skills/jobs: submit/review/block/resume and guarded merge | jobs/PR routes and services | E; orchestration advice retained |
| skills/thread: reply/decline/dispatch, session identity, mention exchange | thread routes/store/runner | E |
| skills/todos: explicit self-scheduling/session/recurrence contract | schedule routes/service | E; intent instruction retained |
| skills/manage-agent: team scope, pending enrollment, quiescent termination | agents routes/registry | E |
| skills/manage-repo: repo mutation and clone refresh | agents routes/context builder | E |
| skills/create-skill: task provenance, package validation and default-hidden eligibility | custom skill routes/validators | E |
| skills/make-worktree: worktree identity and primary checkout mutation detection | guard implementation and `tests/test_worktree_guard.py` | E detection; no OS isolation claim |
| skills/dream: separate invocation callback/candidates and no task completion | dream routes/runner | E; reflection quality remains advisory |
| skills/reflection: report and capture reusable lessons | thread/KB/memory callbacks | A semantic process; callback validation retained |

## G1 — enforcement follow-up retained explicitly

The existing runtime validates submitted local-CI evidence but does not establish
an authoritative task-to-PR-push record, nor independently authenticate a reported
CI command against a durable job receipt. It also does not intercept all shell
execution to require long commands to use jobs. Deleting documentation cannot
solve either limitation. The operational requirements remain in the bundled
`start-task` and `jobs` skills; this cleanup does not mark them code-enforced or
relax their instructions. Authoritative PR/job tracking is a separate behavior
change requiring its own admission, provenance, and recovery design. No inference
from task prose, PR-looking URLs, or absence of evidence is introduced here.

## Verification

Completed in the isolated `refactor/protocol-retirement` worktree on 2026-09-08.
The original checkout and serving environment were not modified. The shared
Python interpreter was used read-only; no editable install or environment sync.

Focused pytest batches passed (overlap exists; counts must not be summed):

- 31: skill freshness and schedule prompts.
- 180: system-contract materialization, workspace adapters, assistant bootstrap,
  and canonical production validation.
- 173: system contracts, context construction, cutover, settings, worktree guard.
- 179: cleanup/schedule scheduler, KB store/routes/deletion, blocked-on-job completion.
- 303 (20 deselected): thread/resume, wake/dream, active policy, production skill
  publication, session/task containment.
- 114: models, capabilities, source resolver, retired adapter-profile coverage and freshness.
- 119: OpenAPI snapshot, settings API, orchestrator, time injection, repo refresh.
- 9: final source/config/hash and frozen packaging inventory regressions.
- 3 real-daemon integration checks: callback task completion, unknown-session
  completion refusal, thread compose/reply/archive. Fixtures used temporary daemon
  homes and fake executors; `UV_NO_SYNC=1` prevented environment mutation.

Collection completed without errors (9,592/9,731 selected before the final frozen
packaging test was added; collection is not execution). A sandboxed asynchronous
scheduler batch was interrupted after a reproducible sandbox shutdown hang;
the focused batches were rerun successfully outside that sandbox. No interrupted
run is counted as passing.

Built the wheel with Hatchling, installed it without dependencies into an isolated
`/tmp` target, and ran Python with `-I` from `/tmp`. Assertions proved installed
module/root identity, all ten skill packages and supporting assets, assistant
knowledge bootstrap with checkout discovery explicitly forbidden, all six session
contexts, both provider-root symlinks and canonical integrity validation. The wheel
contains no `protocol/` tree. A regression executes the real frozen packaging recipe
with compiler stubs and checks its expanded asset inventory for daemon and CLI;
a native macOS executable was not built.

Active runtime/CLI/packaging/guide references were checked and `git diff --check`
passed. Remaining old paths are historical records or compatibility diagnostics.
The legacy non-default config setting refuses startup; operators must migrate any
such deployment override before release. No live org configuration was altered.

TASK-7091 reran the two retained focused command batches at the integrated head
with durable full logs: 334 and 572 tests passed. Its first full local-CI run
then exposed the unclassified historical adapter-profile phrase above; that
documentation defect was repaired before rerunning final verification. The
task-bound host cannot execute the nested-daemon Codex callback integration:
both unchanged main and this candidate fail before the fake executor launches
because inherited task-containment sidecars are correctly refused. This is a
host/harness interaction, not a passing local integration result; exact-head
GitHub workflow evidence remains required.

No full-CI receipt, native macOS build, merge, serving deployment,
production-policy activation, or live cleanup is claimed here. Release
verification remains a separate step using the complete release and its
matching assets/configuration. G1 remains a separately scoped runtime
enforcement follow-up, not a completed enforcement improvement.
