# Orchestrator Contracts

## Conventions

- Type hints on all function signatures.
- `from __future__ import annotations` in every source file.
- Pydantic v2 for structured data.
- `StrEnum` for enumerations.
- Agent names are plain strings; agents are discovered dynamically from `<runtime>/orgs/<slug>/org/agents/*.md`.
- Tests should cover business logic such as escalation rules and audit-log shape.

`README.md` is for end users. `CLAUDE.md` is for repo-wide agent instructions. Implementation and behavior tests own runtime rules. These guides explain them; `docs/superpowers/specs/` retains design history.

When starting a feature, read the relevant design doc first and follow existing patterns in `runtime/orchestrator/`.

## Workspace Cleanup Configuration

`OrgConfig` parses `workspace_cleanup.enabled` as the existing scheduler switch:
it is boolean and defaults to `true`. The enabled scheduler evaluates the daily
local 03:30 occurrence in the org timezone (existing occurrences delimit half-open
windows compared as UTC instants; a nonexistent spring-forward wall time is skipped
and an ambiguous fall-back wall time takes the first `fold=0` instance). It suppresses
when any marker exists at/after the current boundary (including legacy weekly rows) or
any prior marker is unfinished, and it applies no rolling cooldown. Its separate
`workspace_cleanup.reclamation_actions_enabled` key is also strictly boolean,
defaults to `false`, and gates the bounded pre-agent reclamation hook. A true
value does not bypass the hook's registered-owner guard, fresh owner, provenance,
deadline, or consumer admissions. The hook acts only on a third-or-later cleanup
ordinal whose scheduler-created preclaim owner is assigned to a registered
in-memory `TeamsRegistry` agent and reconciles to the invocation's initial
successful `0 -> 1` claim (the first two runs stay report-only); disabling the
key affects later admissions only and cannot revoke an already admitted call.

The daily trigger and manual dispatch share the ONE `workspace-cleanup` TASK
system contract (`requires_repo=false`; source
`runtime/skills/bundled/workspace-cleanup/SKILL.md`). Manual dispatch requires
the exact first line `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)`;
an unmarked manual request is inventory-only. The bundled read-only
`scripts/check_path_use.py` returns only `clear_observation`, `blocked`, or
`unknown`: an authoritative recorded terminal status plus a fresh complete
same-user process scan replaces separate live-session/task-to-process identity
(THR-259 seq171), and a fixed login/session daemon qualifies only by exact
readable process name AND exact bounded cgroup role (THR-259 seq185) and is
deliberately uninspected. Any other unreadable same-user process is `unknown`
and skips; root is outside the scan; positive non-exempt use blocks. This is a
snapshot with disclosed later-opener/data-loss residual, not proof of OS-wide
absence.

The database-only reclamation selection helper is called only by `run_step`'s
pre-agent hook, never by the scheduler. Supplied claim counts other than initial `0 -> 1` refuse before its
per-read admission callback or SQL; a valid supplied pair still needs a fresh
durable-owner read. Its named per-read admission callback is supplied by the
hook's deadline/read accounting; it does not load this config, create a
claim, or call the consumer itself. Every helper SQL/decode observation fails closed as `None`;
this preserves the bounded hook admission accounting without implying SQL
preemption.

## Forward-only terminal task worktrees

The runtime makes one bounded best-effort reclamation attempt after an ordinary
task durably reaches exactly `completed`, `failed`, or `cancelled`, and only
after the applicable process/session/control/job teardown. Shipping owners are
ordinary `_complete`/`_fail`, `_run_agent`'s pre-launch workspace-integrity
failure, daemon-startup liveness failure, successful legacy or V2 zombie
cancellation, the task-cancel route, and portability cancellation. Accepted or
restart completion-recovery settlement, `superseded`, `blocked_on_job`, legacy
normalization, and every unlisted terminal writer do not call this helper.

The only candidate is the registered assigned agent's literal
`repos/happyranch/.claude/worktrees/<task-id>` under the canonical non-bare
primary. Registered-agent ownership, unchanged non-symlink same-device paths,
exact worktree registration and `task/<task-id>` branch, clean status, a HEAD
contained by a durable remote ref, no open or closed-unmerged pull request, no
live session/control/PID/cwd/fd reference, no latest applicable
`worktree-deferred:` risk, and the shared deadline must all be proven. Any
error, timeout, malformed response, missing or ambiguous identity, dirty or
unpublished state, or live reference yields a typed logged preservation result.
Success runs literal non-force `git worktree remove <path>` and never deletes a
branch. The helper changes no task, result, parent, or audit state; signals no
process; enqueues nothing; raises nothing into terminal semantics; and schedules
no retry, scan, or historical reclamation.

## Org Content APIs

`AgentDef` in `runtime/orchestrator/agent_def.py` represents an agent file: markdown with YAML frontmatter parsed/rendered by `parse_agent_text` and `render_agent_text`.

Fields: `name`, `team`, `role`, `executor`, `description`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, `model`, and `system_prompt`. There is no `session_timeout_seconds` field.

**THR-095 single-source-of-truth:** `executor`, `repos`, and `model` are read
exclusively from ``AgentDef`` (the ``.md`` frontmatter). The workspace
``agent.yaml`` is no longer read or written by any org-agent path. See
`docs/agent-guides/runtime-and-configuration.md#agent-configuration-single-source-of-truth-thr-095`.

`runtime/orchestrator/prompt_loader.py` is the API for reading/writing agent files: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent`, `load_terminated_agent`, `list_terminated`, `is_terminated`, and `is_name_unavailable`. Routes and orchestrator code should read through this module against the per-org root.

`TeamsRegistry` in `runtime/orchestrator/teams.py` is seeded from `teams.yaml` and auto-persists on `add_worker` and `remove_worker`. There is no `DEFAULT_LAYOUT`; an org without `teams.yaml` is empty.

## Agent Lifecycle: Enrollment, Approval, and Termination

- **Enrollment.** `manage-agent enroll` creates a pending agent file under `org/agents/_pending/<name>.md`. A founder (or team manager with an active session) may enroll agents only into their own team.
- **Whole-definition update.** A team-manager `manage-agent update` first reads
  the active `GET /agents` roster and uses the target row's `revision` with
  the exact canonical content from that same read as `expected_revision`. The
  route rejects missing, null, malformed revisions with 422 and stale bases
  with 409; after a conflict, the caller must reread and deliberately reapply
  its intended field change. A later roster revision must never bless an
  already-composed stale update.
- **Freshness and conditional recovery.** Repository changes, founder create,
  and approval capture the current prompt/provider after their clone work and
  return 404 if that canonical definition disappeared. Executor switching
  rereads after materialization: an executor or model winner returns
  `409 executor_switch_conflict`, while unrelated fresh fields are preserved.
  If update invalidation fails, its exact original bytes are restored only if
  its written revision is still current; a newer or missing definition stays
  in place and its stale workspace is not restored. Termination's early/late
  archive recovery likewise requires an absent active file and the matching
  owned archive. Enrollment, rejection, and termination make their final
  availability/team/quiescence checks at their respective final boundaries.
- **Scope of these checks.** `teams_lock` and no-`await` segments provide only
  process-local event-loop protection. Synchronous unlocked segments remain
  unlocked; this does not provide global workspace-generation fencing or
  serialize external same-UID/multiprocess filesystem writers.
- **Approval.** `POST /agents/{name}/approve` atomically moves the pending file to `org/agents/<name>.md` and bootstraps the workspace under `workspaces/<name>/`. Approved agents appear in `GET /agents` and `GET /agents/enrollments?status=approved`.
- **Termination.** `manage-agent terminate` archives an approved **non-manager worker** on the caller's team. It is refused if the agent is a manager, belongs to another team, or has live work. Live work includes non-terminal tasks assigned to the agent, already-started thread invocations, firing schedules, running work-hours wakes, running dreams, or pending/running jobs attributable to the agent. If the agent is quiescent, the route:
  - archives the active `org/agents/<name>.md` to `org/agents/_terminated/<name>.md`;
  - archives the workspace `workspaces/<name>/` to `workspaces/_terminated/<name>/`;
  - removes the worker from its team;
  - cancels armed schedules, skips pending wakes/dreams, and declines not-yet-started thread invocations with reason `agent_terminated`.
- **Historic records are retained.** Tasks, task results, audit rows, token-usage rows, thread messages/participants, schedules, wakes, dreams, and archived files are never deleted or rewritten. The agent name cannot be re-enrolled while a terminated record exists, so historical identity remains unambiguous.
- **Fail-closed launch.** The orchestrator and thread runner refuse to launch an agent whose active `.md` file is missing or archived. There is no silent fallback to `claude` for an unknown/terminated agent.
- **Enumeration.** `GET /agents` and the default `GET /agents/enrollments` return active agents only. `GET /agents/enrollments?status=terminated` returns archived enrollment metadata.

## Task Status Vocabularies

Agents self-report `status="completed"|"blocked"` via `happyranch report-completion` (the report verb is unchanged — an agent still self-reports "blocked on jobs"). The orchestrator-owned `TaskStatus` on the `tasks` row is distinct, and under THR-037 Change B (Path B) is: `pending`, `in_progress`, `escalated`, `completed`, `failed`, `cancelled`, or `superseded`. (`blocked` is fully retired as of Phase 3 — see the Path-B spec.)

`block_kind` is the waiting-reason discriminant for an `in_progress` task — *what it is internally waiting on*: `delegated` (waiting on child subtasks) or `blocked_on_job` (waiting on background jobs). `block_kind IS NULL` ⟺ a subprocess is running now. A parent waiting on its children/jobs stays `in_progress` (not `blocked`); the await-founder state is the top-level `escalated`.

`superseded` is a terminal state, peer to `completed`/`failed`. An `escalated` / `in_progress(delegated)` task transitions here when a human-authorized continuation (founder `revisit`, or a founder/manager thread-dispatch) names it in lineage: the predecessor is closed (block_kind cleared, audit cites the continuation root task_id) instead of being re-run. The close never re-enqueues the superseded task; it still wakes a delegated parent via the normal parent-wake path, and the delegated close is gated on all children being terminal so no live sibling is abandoned or SIGTERM'd. It joins every terminal predicate (`TERMINAL_STATES`, `_TERMINAL_TASK_STATUSES`, `_TERMINAL_STATUS_TO_EVENT`) and is completion-class for the thread task-followup: a thread-originated task that is superseded emits its `_maybe_post_thread_followup` system message (`task_completed` kind) just like a normal completion. The thread-dispatch supersede is manager-authorized only — a worker self-dispatch naming `resolves` is rejected (`403 thread_supersede_not_authorized`); the predecessor is never auto-closed by an unauthorized dispatch. Query the backlog with `happyranch tasks --status escalated` or `happyranch tasks --status in_progress --block-kind delegated`.

## Derived Work-Status Summary (TASK-5522)

`GET /tasks/{task_id}` carries a read-only `work_status` envelope key derived
server-side (`runtime/daemon/work_status.py`) from the task record plus its
existing audit rows — **no schema change, no synthetic audits, no background
monitor**. It exposes only: the current-session start (latest assigned-agent
`session_start` audit), the last heartbeat with an explicit freshness label,
and the timestamp + concise agent-written message of the latest current-
session `progress` receipt. Chain of thought, command stdout, workspace
paths, session ids, and arbitrary audit payloads are never exposed.

State machine (live-task shape = `in_progress` + `block_kind IS NULL`):

| state | meaning |
|---|---|
| `newly_started` | fresh heartbeat; no current-session receipt; session start < 5m old |
| `recent_progress` | fresh heartbeat; latest current-session receipt < 5m old |
| `stale_no_receipt` | fresh heartbeat; no receipt; session start ≥ 5m old |
| `stale_old_receipt` | fresh heartbeat; latest receipt ≥ 5m old |
| `heartbeat_stale` | live shape but heartbeat ≥ 60s old (existing zombie-reaper freshness semantics) |
| `heartbeat_unavailable` | live shape, no heartbeat observed |
| `unavailable` | cannot derive (missing session_start, unassigned, malformed historic data) |
| `not_applicable` | terminal / pending / escalated / in_progress parked-on-block (`reason` discriminates) |

Policies: `STALE_PROGRESS_AFTER_SECONDS = 300` (5-minute display/derivation
policy — it never reaps or acts); heartbeat freshness reuses the existing
60-second semantics (`2 × HEARTBEAT_INTERVAL_SECONDS`). The current-session
lower boundary is the latest assigned-agent `session_start`; a prior
session's `progress` receipts must never satisfy the new session. Labels say
what is observed — a fresh heartbeat is never presented as substantive
progress, and absent/malformed data is surfaced as unavailable, never
fabricated. Both `happyranch details` and the Tasks UI render this summary;
`runtime/skills/bundled/start-task/SKILL.md` §5 makes the corresponding worker
checkpoint policy concrete.

### Post-deploy operational measurement (not a shipping gate)

The per-task states above make **individual** tasks observable; they do not,
by themselves, measure the population metric this change is meant to move.
That metric is the **share of COMPLETED tasks whose wall-clock duration is
strictly greater than 15 minutes**, measured by the read-only per-org SQL
procedure below — never from `progress` audit rows. `progress` receipts /
`work_status` are a diagnostic companion measure only (see "What this
contract does and does not make observable" below).

**Pre-deploy baseline (authoritative).** Immediately before this change
shipped, **277 of 600 completed tasks (46.2%)** exceeded a 15-minute
wall-clock duration. **46.2% — never 55%** — is the baseline this deployment
is measured against. The post-deploy operational/release target is **<20%**
of completed tasks exceeding 15 minutes, evaluated inside the explicitly
defined post-deploy observation window below. That target is an
**operational post-deploy goal only**: it is NOT a PR shipping, approval,
merge, or CI gate, and no CI or merge check enforces it.

**Observation procedure (read-only SQL, per org).** Each org is measured
independently against its own database — the org boundary is the per-org
SQLite file `<runtime>/orgs/<slug>/happyranch.db` (the daemon's
`OrgPaths.db_path`, `runtime/orchestrator/_paths.py`). Orgs are never
pooled: the evaluator must substitute each org's actual storage scope and
timestamps. The window is **half-open** `[window_start, window_end)` and
uses the task **completion time** (`tasks.completed_at`) for membership; the
numerator applies the same bounds and additionally requires a wall-clock
duration **strictly greater than 15 minutes (900 seconds)**. Wall-clock
duration is the difference between the persisted completion and creation
timestamps (`tasks.completed_at` − `tasks.created_at` — both columns are
non-null on every `completed` row and store ISO-8601 UTC text, so
`julianday(...)` arithmetic applies directly; this is the full lifecycle
wall clock from task creation to completion, an upper bound on active work
time). Run the query once per org DB file:

```sql
-- Per-org read-only observation: completed-task wall-clock > 15 min share.
-- Open the org's DB read-only:  sqlite3 "file:<runtime>/orgs/<slug>/happyranch.db?mode=ro"
-- Bind the evaluator's actual values (sqlite3 CLI: .parameter init, then
-- .parameter set :window_start '<utc-iso>'; .parameter set :window_end '<utc-iso>'):
--   :window_start  post-deploy observation window start, inclusive (UTC ISO)
--   :window_end    post-deploy observation window end,   exclusive (UTC ISO)
WITH windowed AS (
  SELECT (julianday(t.completed_at) - julianday(t.created_at)) * 86400 AS dur_seconds
  FROM tasks AS t
  WHERE t.status = 'completed'
    AND t.completed_at >= :window_start
    AND t.completed_at <  :window_end
)
SELECT
  COUNT(*)                                           AS denominator,
  SUM(CASE WHEN dur_seconds > 900 THEN 1 ELSE 0 END) AS numerator,
  CASE
    WHEN COUNT(*) = 0 THEN NULL  -- zero denominator => N/A, never 0%
    ELSE ROUND(
      100.0 * SUM(CASE WHEN dur_seconds > 900 THEN 1 ELSE 0 END) / COUNT(*),
      1
    )
  END                                                AS pct_over_15_min
FROM windowed;
```

Procedure notes:

- **Per-org, never pooled.** Re-run the query against each org's own DB file
  and report each org's `denominator` / `numerator` / `pct_over_15_min`
  separately. Do not aggregate orgs into one denominator.
- **Strict inequality.** The numerator counts `dur_seconds > 900`; a task
  whose duration equals exactly 15:00.000 does not count.
- **Half-open window.** Membership is `completed_at >= :window_start` AND
  `completed_at < :window_end`; a task completing exactly at `:window_end`
  belongs to the next window.
- **Zero denominator.** An org/window with no completed tasks yields
  `denominator = 0` and the percentage is **N/A** (the `CASE` yields NULL) —
  never 0%, which would falsely claim the target was met.
- **Read-only.** The query contains no writes; open the DB in read-only mode
  (`?mode=ro`) or run it against a snapshot/copy.

**Post-deploy observation window.** The operator defines a fixed half-open
window at deployment time — for example the 30 days following the deploy
timestamp — and evaluates the same query with `:window_start` = deploy
timestamp and `:window_end` = window end. The pre-deploy baseline 277/600
was measured with the same definition over the pre-deploy completed-task
population.

**What this contract does and does not make observable.** `action=progress`
remains optional and is the only persisted agent-written substantive receipt
in the current contract. When it is absent, the server-derived `work_status`
explicitly reports `newly_started` (session under 5 minutes) or
`stale_no_receipt` ("no substantive update recorded") for a live session —
heartbeat is liveness evidence only and is never substantive work. That
absence classification makes silence observable for operational follow-up
(a live-but-silent task is visibly distinguishable from one with recent
substantive progress). However, a progress-only audit query cannot prove the
implementation moved the >15-minute completion-duration metric: receipts are
optional and the population metric is defined over completed-task wall-clock
durations, not receipts. The primary metric is therefore measured from
completed-task wall-clock durations by the per-org query above;
`progress` / `work_status` may be used only as a diagnostic companion
measure.

## Manager Decision Contract

The completion route authenticates the active task/session binding and validates
submitted job waits and structured evidence before persisting a report.
`run_step.py` consumes it into a daemon-owned transition; an agent's `blocked`
report is distinct from the task's stored state. `pr_ci_merge.py` owns guarded
merge verdict extraction: a non-null canonical structured verdict is primary;
unusable structured values cannot fall back to prose. Its tests cover null legacy
fallback and contradictory evidence. A submitted `local_ci` object is strictly
validated by `LocalCiEvidence`, but is not an independently verified CI receipt,
and the daemon does not infer that a task pushed a PR from its prose.

Team-manager completion payloads carry two fields:

- `summary`: human-readable prose stored on `task_results.output_summary` and rendered in details, audit logs, and `task_history.md`.
- `decision`: a JSON `NextStep` object stored on `task_results.decision_json` and parsed directly by `Orchestrator._parse_next_step`.

The child-task brief field in a `delegate` decision is `prompt`, not `brief`. Pydantic v2 silently ignores extras, so `"brief"` creates an empty-brief child task.

Wire fields are defined by `CompletionBody` in `runtime/daemon/routes/tasks.py`;
internal decisions by `NextStep` in `runtime/models.py`. The bundled `start-task`
skill explains usage. The two models serve different boundaries; do not copy one
as a second HTTP schema.

### Active team authority policy

`authority_policy_store.py` owns immutable releases, activation history, candidate
pins, and their transactional linkage. Every uniquely registered live team-manager
launch binds the exact rendered policy to the session; the hook consumes that
binding, never a newly selected current activation. Task, fresh/resumed thread,
wake, dream, and schedule entry points independently revalidate the live AgentDef
and exact single `teams.yaml` registration before selector access or binding. The
bound team, not current registry state or caller input, is carried through attempt,
candidate, evaluation, finalization, publication, and spend identities.
`authority.py` validates authenticated manager
self-evaluation for active-policy sessions and applies daemon-owned cancellation,
budget, lineage, protected-boundary, replay, and CAS checks. Semantic evidence is
advisory to those checks. The single-use continuation envelope grants only the
same-root lifecycle. Static-policy compatibility remains separate. Policy routes
provide the founder-authorized release/activation UI; code landing does not
activate a production policy. Authority hook/store/route/envelope tests cover the
negative cases. There is no universal post-session detector for arbitrary
external side effects or role-based pathname enforcement.

#### THR-229 current candidate contract

The open PR878 candidate joins the v2 path end to end. An authenticated v2
launch binding selects the strict completion carrier; the persisted dual
assessment drives one automatic claim/evaluate/consume/finalize sequence; exact
post-final settlement publishes one tagged generation; the real queue and
Dispatcher admit it once; and the next real completion result spends, claims,
applies, and acknowledges the single-use envelope. Startup and zombie recovery
may only rediscover/refuse or reconcile those same durable identities. There is
no second evaluator, magic reason, clause identifier, adverse-review veto, or
raw-DDL veto. `REQUEST_CHANGES`, partial-work observations, and the accepted
historical schema-layout inequality remain diagnostics; current owner/session,
cancellation, active-work, budgets, protected drift, atomicity, replay, and
closed audit evidence remain authoritative.

The dedicated eligible-manager page is available uniformly to every valid
registered manager, edits the two values only as a pair, and uses the
authenticated team-scoped selector projection/history. The server owns the
neutral starter bodies and deterministically derives each team's starter ID and
title; there is no Engineering behavior branch. Legacy compatibility is data
driven by `POLICY_BY_TEAM`: an absent definition projects a null template and
refuses both legacy writers before store access. Existing Engineering persisted
identities remain unchanged. Landing or merging source
does not save or activate a production policy. A future activation requires
compatible binaries everywhere and drained old manager launch and completion
consumers; rollback is to compatible code, not an old consumer against new
persisted v2 state. Deployment, activation, and natural production continuation
are not claimed by this source contract.

The checkpoint narrative below is preserved as historical implementation
provenance. Every statement in a named checkpoint that a later stage, automatic
hook, recovery caller, or editor was dark, future, deferred, or unimplemented
describes that checkpoint only and is superseded by the current candidate
contract above.

#### Historical THR-229 checkpoint record

The staged THR-229 v2 value contract introduced two
editable release texts, `What to escalate` and `What not to escalate`, which
are a strict pair: `AuthorityPolicyV2Release` (in `runtime/models.py`) forbids
unknown fields, requires both texts together, bounds the title/text scalars and
the canonical UTF-8 byte size, rejects NUL/surrogate/blank strings and
non-lowercase-hex digests, and derives the exact approved immutable preimages
and digests (contract, release, create/activation request, activation,
selector, causal-result, candidate/envelope/attempt/notification). A pure
assessment helper treats an escalate match as dominant, permits continuation
only for clear non-escalate plus continue applicability, and otherwise fails
closed; uncertainty always outranks an escalate match. These value-layer
results are advisory data, not launch authentication or continuation authority.

Checkpoint B1 additionally lands the accepted control-plane storage as private
callable code. `runtime/infrastructure/database.py` owns the additive
`authority_policy_v2_releases`, `authority_policy_v2_activations`,
`authority_policy_active_selector`, `authority_policy_active_selector_history`
and `authority_policy_v2_control_audit` tables plus the transaction-owning
authenticated `ensure_authority_selector(team)`, paired create+activate and
existing-release activate/rollback methods under `BEGIN IMMEDIATE`; request
receipts and audits live in the accepted control-audit persistence.
`authority_policy_store.py` exposes them as a thin typed facade that never
begins or commits a transaction. The v2 byte decoder rejects UTF-16/UTF-32
input and JSON `NaN`/`Infinity`. No startup, route, launch, prompt, completion,
legacy activation writer, queue/reaper/consumer or UI surface reads or writes
the selector yet, and the remaining nine approved candidate/binding/envelope/
recovery tables belong to their actual later transactions. This isolated,
callable storage is deliberately not a shippable mixed-writer mode: the later
convergence unit must move every legacy and new selection writer onto one CAS
transaction before integration.

Checkpoint B2a makes the accepted cross-family controls authenticated. Every
shared read boundary — live selector, immutable selector history, by-id lookup,
`ensure_authority_selector` and the v2 create/activate replay receipts — now
authenticates the accepted control-audit preimage/receipt links in addition to
the release/activation/selector chain: a missing, mutated, duplicated,
mismatched or orphaned audit, or a type-valid receipt whose durable activation,
release or selector row is absent, refuses without reconstructing, allocating
or replacing anything, and orphan initializer audit residue refuses
initialization instead of writing a replacement. A non-initial `legacy_v1`
selector is bound by the regular APS preimage with its real predecessor; only
the initial empty epoch-0 / legacy epoch-1 selector uses the frozen initializer
preimage, and the exact three-arm CHECK is unchanged. Selector-aware legacy
`activate` (a newly activated v1 release) and `reactivate_rollback` (an
authenticated previously selected v1 activation) run under the same
`BEGIN IMMEDIATE` selector CAS as v2; the v2→v1 reactivation appends a NEW
selector/history/control audit and never appends, renumbers or reseals the
original legacy activation. Legacy family epochs and team selector epochs
remain separate counters. No startup, route, launch, prompt, completion,
queue/reaper/consumer or UI surface reads or writes the selector yet: the
shipping v1 writers in `runtime/daemon/routes/authority_policy.py` still bypass
it and B2b must converge them, so this remains an intermediate unmerged
storage/control seam rather than a shippable mixed-writer mode.

Checkpoint C3a lands the accepted v2 CONSTRAINT-SENSITIVE schema-integrity
prerequisite as a separate, read-only, callable seam in
`runtime/orchestrator/authority.py`:
`capture_authority_policy_v2_schema_integrity(db)` and
`recheck_authority_policy_v2_schema_integrity(evidence, db)` returning the
narrow typed `AuthorityPolicyV2SchemaIntegrity` evidence from
`runtime/models.py`. Its reference is constructed independently of the
candidate database — fresh current source plus ONLY the two accepted exact
migrated `threads`/`thread_messages` ordered-layout substitutions and their
index-cid consequences — and the oracle compares the complete non-internal
object inventory (full table SQL including CHECK/UNIQUE/FK expressions, ordered
`table_xinfo`, `foreign_key_list`, complete `index_xinfo` including expression
sentinels/collation/key flags/cid, `index_list` origin/unique/partial, explicit
index SQL/predicate and full trigger/view SQL; autoindex constraint metadata is
retained and only rootpage/allocator and SQLite's reserved internal
`sqlite_`-prefixed objects are ignored). The reserved prefix is matched as that
exact literal, case-insensitive prefix — never a SQL `LIKE 'sqlite_%'` pattern
whose `_` is a wildcard — so a legal user object that merely resembles the
internal namespace (for example `sqliteXunreviewed`) is still inventoried and
refused as unexpected. Every candidate read and the frozen raw digest run inside
ONE `Database.coherent_read_view()`: the shared-connection lock is held across
the whole capture and one SQLite read snapshot is pinned, so a commit on an
independent connection can neither split the inventory from the digest nor be
authenticated by a stale inventory — it can only make the subsequent recheck
refuse. It requires `PRAGMA integrity_check` exactly `ok` and zero
`foreign_key_check` violations, then freezes the validated candidate's ACTUAL
raw `sqlite_master` DDL digest. A recheck denies ANY later raw-digest drift
(including a switch to the other accepted layout) and a failed/unavailable
capture can never become a successful recheck; unknown layouts and read errors
fail closed with bounded machine-readable diagnostics. The result is integrity
EVIDENCE only — never policy authority, a clause match or a grant — and the
candidate is never repaired. The legacy v1
`_release_schema_digest`/`_live_schema_digest`/`_server_evidence`/
`_server_fact_clause`/`_during_attempt_drift_clause` behavior and all callers
are unchanged, and this seam is not yet wired into the authority hook. The
checked-in full historical schema fixture
`tests/fixtures/authority_v2_historical_schema.json` (with reconstruction
support in `tests/authority_v2_historical_schema.py`) rebuilds the whole old
schema and opens it through the actual current `Database` migration path; the
R3 shipping venue runs over the migrated DB as well. The persisted
candidate/pin/evaluation/continuation consumer, the corrected
adverse/partial/raw-DDL diagnostics and the recovery/generation admission
fences remain staged later units.

Checkpoint C3b lands the durable v2 candidate/pin claim and the SEPARATE
claim-audit stages. The Database owns synchronization and the transaction
boundary and adds exactly three additive tables: `authority_policy_v2_candidates`
(K), `authority_policy_v2_pins` (P; its identity equals the candidate identity)
and `authority_policy_v2_candidate_audit` (a closed claim-stage event with a real
candidate FK and an immutable canonical payload; no free-form rationale/model
transcript/credential). The candidate identity is exactly the frozen R2 claim-key
preimage (`APV2C-` + the claim-key digest) and `claim_key` uniqueness prevents a
second candidate for the same causal tuple. This PR's own new
`authority_policy_v2_attempts` stage CHECK is completed to
`admitted|claimed|claim_audited`; no released column is altered, dropped or
reinterpreted and `audit_log` keeps its existing action scope.

`Database.claim_authority_policy_v2_candidate` refuses transaction nesting
with the closed code `transaction_owned` BEFORE it would `BEGIN`, `ROLLBACK`
or invalidate the live owner whenever the caller already owns a transaction —
the caller's transaction and its pending work are left untouched and the two
stage commits stay independent (no merged transaction, no silent savepoint and
no changed R4 durability) — and otherwise runs ONE synchronized
`BEGIN IMMEDIATE` (the independent C3a reference is constructed OUTSIDE it and
its frozen raw digest is re-validated while the transaction is held) that
re-reads and authenticates the actual immutable admitted result: exact
attempt/root/team/manager/session/result plus `origin_boot_id`/`owner_attempt_id`,
`unfinalized`/`admitted` state, the single authenticated `admitted` audit, the
actual task (`in_progress`, null `block_kind`, null `cancelled_at`, current
owner/session), the causal result row/task/agent/session AND its normalized body
(the persisted `_manager_self_evaluation` carrier must re-hash to the admitted
`assessment_digest`; the CRD is only the row-identity digest), the immutable
launch binding, the pinned release/activation/selector identity through its own
pinned epoch (a later legitimate activation never replaces or invalidates an
older pin, and corrupt/mixed pinned history refuses with no fallback to today's
policy) and the resolved provider/executor/model. Claim also freezes the ACTUAL
claim-time schema evidence (raw/inventory digest and object count) and bounded
read-only permission-surface evidence — read through the narrowly scoped
server-side reader bound on the Database as `reader(agent)`, never a
caller-supplied allow/deny boolean or precomputed digest — onto BOTH K and P;
an unbound/read-failed/malformed read fails closed and can never become a
sentinel digest, and these evidence columns are not claim-preimage inputs. The
applicable mechanical
eligibility predicates — revisit lineage, active chain/fanout, blocked job,
successor root and the revise-budget ceiling — are re-derived from the persisted
task row; no caller boolean substitutes for a server fact, and the legacy
adverse-review/partial-work/raw-DDL `_server_fact_clause` diagnostics are never
a v2 veto or a phrase/clause unlock. On success it inserts K+P and advances J to
`claimed`, preserving R and a0 and NOT appending the a1 claim event.

A separate second transaction (`audit_authority_policy_v2_candidate_claim`)
RE-AUTHENTICATES the complete evidence instead of trusting K/P — the causal
result row/body, immutable binding, authenticated pinned release/activation/
selector prefix, the full candidate/pin/attempt/release/binding joins
(provider/executor/model/version/digest/boot/owner), the prior required a0 and
the current task ownership/cancellation — and rechecks the ORIGINAL frozen
claim-time schema/permission evidence under its owned transaction (no
recapture-and-rebaseline). It inserts exactly one candidate claim event plus the
required `claim_audited` result-stage evidence and advances J to `claim_audited`
atomically. A failed claim leaves J admitted/a0 with no K/P; a failed claim-audit
preserves the claimed K/P with no a1. An owned-stage failure, a genuine
cancellation/replacement, or a schema/permission-drift refusal by the authentic
uninterrupted owner poisons the winning token so a later retry cannot become a
fresh authority; an unauthorized/stale/duplicate contender (wrong boot, wrong
owner, wrong tuple, second connection, duplicate call, transaction-nesting
refusal) is classified before liveness and never poisons the winner. Refusals return only a bounded
`AuthorityPolicyV2StageOutcome` with a closed refusal code: this checkpoint
performs NO refusal housekeeping and NO evaluation/consume/envelope/finalization,
does not mutate tasks into Pending/Escalated, and the later THR-229
refusal-housekeeping consumer owns any durable refusal/settlement. The
admission-audit authentication is scoped to exactly one authentic `admitted`
event inside its own stage, so later legitimate stage events never invalidate
the single immutable a0; exact CLI/HTTP transport retries after progression stay
read-only success and a changed client payload still refuses. `AuthorityPolicyStore`
exposes only thin forwarders (`claim_v2_candidate`, `audit_v2_candidate_claim`,
`bind_v2_permission_surface_reader`, the authenticated K/P/a1 readers) and never
commits or nests. The shipping
authority hook remains fail-closed (ESCALATE) until the complete
consumer/refusal/finalization path lands.

Checkpoint C3c lands the four accepted pre-final evaluation and single
consumption stages: `evaluate_authority_policy_v2_candidate`,
`audit_authority_policy_v2_candidate_evaluation`,
`consume_authority_policy_v2_candidate` and
`audit_authority_policy_v2_candidate_consumption`. The Database owns each
synchronized `BEGIN IMMEDIATE`/commit/rollback and each method refuses caller
transaction nesting with the closed code `transaction_owned` BEFORE it would
begin, roll back or invalidate the live owner, so the four R4 durable boundaries
stay separate and the caller's pending work survives. The additive
`authority_policy_v2_evaluations` (V) table stores exactly one immutable
evaluation per candidate whose identity equals the candidate ID; it holds the
derived clause-free outcome, the admitted assessment digest and either the
bounded sanitized assessment evidence or the honest invalid-assessment
diagnostic code. The attempt stage CHECK/transitions are completed to
`admitted|claimed|claim_audited|evaluated|evaluation_audited|consumed|consumed_audited`,
the candidate audit gains the `evaluated`/`consumed` closed events, and the
candidate table gains a forward-only `created -> evaluated -> consumed`
lifecycle guarded by a precise trigger: every identity/frozen-evidence column
stays immutable and only the lifecycle stage may advance. The evaluation
transaction re-reads the persisted sanitized `_manager_self_evaluation` carrier,
validates its full bound identity and both assessments, derives the outcome ONCE
through the existing pure `derive_authority_policy_v2_assessment_outcome` (a
diagnostic carrier stays `invalid` and is never invented into a valid
assessment), inserts one V and advances K/J atomically; the separate evaluation
audit appends one candidate `evaluated` event plus the `evaluation_audited`
result-stage evidence and advances J without re-deriving the outcome;
consumption re-authenticates a0+a1+a2 and the stored V and CASes K/J to
`consumed` exactly once with no second model call; the consumed audit appends
one candidate `consumed` event plus `consumed_audited` evidence. Earlier
committed stage evidence survives every later failure and the shipping authority
hook remains fail-closed (ESCALATE) until the later THR-229
refusal/reaper/recovery/finalize/settle/publish/admit/spend consumer and the
editable-pair editor/browser path land and merge.

The C3c correction tightens the four pre-final boundaries without changing the
accepted table/identity contract. First, BOTH halves of every required prior
stage audit are authenticated at every public evaluation, evaluation-audit,
consume and consumed-audit boundary: exactly one closed
`authority_policy_v2_result_stage` `audit_log` event for each required stage
(`admitted`, `claim_audited`, and — from consumption onward — `evaluation_audited`)
with the exact closed payload key set, the immutable attempt identity, the
candidate ID for the later stages and the required `unfinalized` finalization
marker, alongside the sibling `authority_policy_v2_candidate_audit` event. The
audit created by the transaction currently running is never required, `a0`
admission replay stays scoped to `admitted`, and a missing, duplicated, mutated,
foreign-candidate, extra-key or malformed event is a bounded refusal that writes
no V/audit/advancement and never repairs by reinserting. Second, the persisted
manager decision is inspected separately from its dual assessment: the
`decision_json.action` must be the accepted root escalation action `escalate`, so
a missing/malformed/non-escalate decision (ordinary `delegate`/`supersede`/`done`
etc.) or a decision drifted between stages refuses the continuation path while
`_server_fact_clause` prose, clause/candidate preimages and legacy v1 behavior are
untouched. Third, the retained current mechanical eligibility — root-only, task
ownership/cancellation, active chain/fanout, blocked job, revisit/successor
lineage and the current server-owned `max_revise_rounds` ceiling against the
persisted `tasks.revision_count` — is re-derived from the authenticated persisted
state at every stage boundary (the scalar ceiling is threaded through the
existing `authority_policy_v2_claim_eligibility(orch)` seam, never an invented
allow boolean), not only once at claim; ordinary `REQUEST_CHANGES`/partial-work
diagnostics and valid historical DDL remain non-vetoes.

Checkpoint C3d1 lands the accepted terminal pre-final refusal and exact
recovery-receipt housekeeping. `AuthorityPolicyV2Attempt` may now finalize as
`refused`/`owner_lost` with a closed `refusal_code` while retaining its greatest
committed `stage`, exact immutable identities and canonical evidence; an
unreleased forward-only trigger makes a finalized attempt terminal (it can never
reopen or advance), and the candidate-audit closed event set gains `refused`.
`Database.finalize_authority_policy_v2_attempt_refusal` owns ONE synchronized
`BEGIN IMMEDIATE` transaction over exact J/R, the current task and the optional
exact recovery receipt Q, refusing caller transaction nesting with
`transaction_owned` BEFORE it would begin, roll back or invalidate the live
owner. It authenticates the immutable ATTRIBUTION (exact causal result
row/root/agent/session and the immutable launch binding, plus the attempt and any
candidate reference) FIRST, before any terminal success or receipt
classification, and never the failed CONTINUATION evidence whose absence caused
the refusal (a missing stage audit, a drifted assessment/decision or a frozen
schema/permission drift is not required and is never reconstructed). For the
still-current causal owner (`in_progress`, null `block_kind`, not cancelled) it
writes the closed refusal result-stage event, a candidate `refused` event if K
exists, the normal `escalation` audit and the bounded `completion_report` refusal
audit, then CASes the task to `escalated` and J to `refused`; if Q is
`callback_accepted` and exactly matches
task/agent/recovery session/accepted result/session it is settled to
`callback_consumed` in the SAME transaction, and an explicit recovery assertion
with no actual matching Q fails closed (`receipt_missing`) instead of
manufacturing recovery-shaped evidence. Ordinary callbacks keep Q absent and
genuine ordinary absence stays ordinary by minting only ordinary completion
evidence.
A cancelled/terminal/replaced owner preserves the winning task row exactly and
records the old attempt `owner_lost`/`cancellation` disposition and audits,
settling only an exact obsolete matching Q. No successor/new root, envelope,
notification, dispatch generation or queue effect is created, and raw model prose
never enters a bounded refusal diagnostic. Live-owner safety binds the trusted
current daemon-process identity through `bind_authority_policy_v2_process_boot_id`
(never a caller allow boolean): only the authentic in-memory owner token, an
old-boot attempt, or a server-written durable failed-stage obligation may
finalize; a same-boot attempt with no token (a second Database instance) cannot
prove the winner is dead and returns bounded `housekeeping_pending`. Owned-stage
failure paths record that durable obligation before poisoning the token, and no
process-local liveness is reconstructed on reopen. A failed refusal transaction
is refusal-only: it rolls the WHOLE terminal transaction back, preserves the
original exception truthfully, and — only when the caller presented the exact
authentic live-owner token — poisons that token (prohibiting any later
claim/evaluate/consume/audit advancement and final mint) and records the same
bounded best-effort durable obligation; a process-local failure-ownership marker
keeps safely attributable housekeeping retry possible even when the durable
diagnostic could not be written, and it is never reconstructed from durable
UUIDs. Malformed/foreign/nested/duplicate contenders are classified before
poisoning and never poison or finalize a valid winner. Already-finalized attempts
return a read-only `already_refused` exact replay only when the complete
identity-scoped terminal evidence set authenticates as ONE closed set — the exact
causal result/root/manager/session and immutable launch binding authenticated
FIRST (never the failed continuation body/assessment), the refusal result-stage
event, exactly one bounded completion audit (a duplicate/conflicting code is not
filtered out of the uniqueness check), the normal escalation audit for the
still-current-owner `refused` outcome (never required for the `owner_lost`
outcome that preserves a different winning task), and the candidate `refused`
audit when K exists; deleted, duplicated, mutated, conflicting or malformed
terminal evidence is never repaired and mints no second audit. Receipt identity
comes from a real Q: an explicit `recovery_session_id` with no actual matching Q
fails closed with the bounded `receipt_missing` pending code and fabricates no
recovery-shaped completion evidence, a genuine ordinary absence stays ordinary,
and when the stored completion carries the exact recovery fields the replay
additionally requires the exact `callback_consumed` Q with the exact
task/agent/recovery-session/result/result-session tuple (a
deleted/transition-only/replaced Q is missing evidence, never "ordinary"). Callable
read-only discovery (`list_authority_policy_v2_unfinalized_attempts`,
`get_authority_policy_v2_housekeeping_target`) surfaces unfinalized attempts
including a failed claim with no K, and the durable J/R or recorded obligation
remains discoverable after a refusal transaction failure. The store exposes thin
forwards and never commits. The shipping authority hook remains fail-closed; the
later publication/admission/spend transitions and
the startup/reaper/run-step automatic discovery wiring remain separate units, so
the dual-text feature remains unaccepted.

Checkpoint C3d2 lands the accepted R4 finalize and settle-receipt steps 1-2 on
the same unmerged draft PR, adding exactly the three remaining approved additive
tables. `authority_policy_v2_continue_envelopes` (E) is unique by candidate and
repeats/authenticates the complete pinned tuple; its identity is exactly
`APV2E-` + H({"candidate_id":C,"kind":"continue_envelope"}) and it is inserted
ACTIVE, with only a forward-only active -> consumed lifecycle (the later spend
unit owns that transition). `authority_policy_v2_recovery_notifications` (N) is
the continuation generation G, identity exactly `APV2N-` +
H({"envelope_id":EN,"kind":"continuation_notification"}), unique by envelope and
by exact causal tuple, carrying the closed
`needed|publishing|published|admitted|settled|invalidated` lifecycle,
publication attempt/boot/lease and a nullable reserved next session.
`authority_policy_v2_root_dispatch` (D) is keyed by root, names one generation
with `pending|admitted|retired`, preserves the expected causal owner/session and
admits only one non-retired generation. ONE
`Database.finalize_authority_policy_v2_continuation` synchronized
`BEGIN IMMEDIATE` transaction requires J unfinalized/`consumed_audited`, the
consumed K, the exact P/V joins, the persisted V `continue_applies` outcome
(never re-derived and never a second evaluator call), the original uninterrupted
process-local winning owner, an eligible current root/task and BOTH halves of
a0/a1/a2/a3 with full closed key/type/value/cardinality checks. It inserts the
active E, the final candidate/task/hook and closed `continued` result-stage
audits, the N `needed`, the D `pending(G)`, changes the task to
Pending/null `block_kind` preserving the causal owner/session, and CASes J to
`continued`. D is inserted when absent, or CASed from its EXACT retired previous
G only; a pending/admitted pointer, an existing live v1 envelope or an existing
v2 envelope for the candidate is never replaced. A required audit/insert/update
failure rolls the whole final transaction back, retaining the previously
committed consumed K/P/V/a0..a3 and the in-progress task; a genuine finalization
failure poisons only the authentic winning owner and selects C3d1 refusal-only
housekeeping (durable failed-stage obligation plus a process-local safe fallback
when that diagnostic cannot be written), and no remint, re-evaluation or reopen
follows because persisted UUIDs never restore liveness. Exact successful causal
replay authenticates the final evidence read-only (`already_continued`) or goes
to post-final housekeeping; it never allocates another E/N/D, spends E or resets
N, and a later legitimate selector activation never invalidates the pinned final
tuple. A SEPARATE `Database.settle_authority_policy_v2_continuation_receipt`
transaction/read authenticates the complete final J/K/P/V/E/N/D and final
audits/attribution before settlement. A genuine recovery requires the REAL exact
root/agent/recovery_session_id/accepted_result_id/accepted_result_session_id Q
and CASes `callback_accepted` -> `callback_consumed` together with the required
closed completion and `authority_policy_v2_recovery_settled` audits; an
already-`callback_consumed` exact Q independently authenticates the complete
existing settlement/final evidence read-only and returns `already_settled_exact`
(a transition-only false return is never treated as absence and missing evidence
is never synthesized); an explicit recovery assertion with no matching Q returns
the bounded `receipt_missing` pending code and mutates nothing; and a genuine
ordinary absence stays ordinary and requires the real ordinary completion
evidence at the accepted completion-consumer seam instead of a fabricated
recovery-shaped completion. Identity-scoped audit rows are enumerated before
closed-payload checks so a different code/discriminator cannot hide a duplicate.
A failed settlement retains Pending/E/N/D/J and `callback_accepted` and permits
ONLY exact settlement retry, never policy work or a mint. The store exposes thin
forwarders (`finalize_v2_continuation`, `settle_v2_continuation_receipt`,
`get_v2_continue_envelope`, `get_v2_recovery_notification`,
`get_v2_root_dispatch`) that never commit. Publication, generation admission,
envelope spend, the startup/reaper/run-step wiring and the production
authority-hook continuation remain separate units; the shipping hook still
fail-closes to ESCALATE and the dual-text feature remains unaccepted.

C3d2 correction (same unmerged draft PR). The exact post-final causal replay and
settlement now authenticate the COMPLETE durable evidence read-only through ONE
shared post-final authenticator — the exact J/R/K/P/V joins, the authentic
immutable binding plus pinned release/activation/selector history, the persisted
assessment/decision identity, the consumed candidate/evaluation, BOTH halves of
a0..a3 and the complete final E/N/D + final audit set — while deliberately NOT
requiring the pre-final live-owner token, an `in_progress` pre-final task, a
fresh mechanical-eligibility re-derivation or today's selector equality, so a
legitimate finalized replay/reopen authenticates without restoring a live
pre-final owner, re-evaluating or reminting. A deleted, mutated, duplicated or
foreign admitted/prior-stage audit, or a changed `task_results` session/decision,
returns bounded pending/refusal read-only with no Q/audit/allocation change even
on reopen; settlement additionally authenticates the actual Pending causal
owner/cancellation and the E/N/D stage. Settlement audit contents are bound
value/type-sensitively (`true` never equals integer `1`) to the exact
authenticated attempt/candidate/envelope/notification/generation/result/root/
agent/session and the actual persisted completion projection, and potentially
identity-related completion/settled rows are enumerated BEFORE discriminator
filtering so malformed/conflicting/duplicate rows cannot disappear into an
apparent absence (unrelated genuine historical sessions are never duplicates);
corruption refuses read-only and a consumed Q stays consumed. Initial
`callback_accepted` settlement validates the coherent pre-state — no
settlement-owned completion or settled evidence for the exact result — before
its atomic Q+completion+settled write, and targeted per-boundary failure
injection proves rollback retains the accepted Q and every prior final row for
one safe exact retry. Ordinary completion evidence is now scoped to the actual
causal result/session: the real `Orchestrator._log_step_result` producer adds the
v2-only `_result_row_id`/`_result_session_id` attribution at the completion seam
(the v1/legacy path and `AuditLogger` bytes are unchanged), so a legitimate
earlier manager completion no longer blocks a valid current result and an
identical old-session body can never stand in for the current event.

C3d2 evidence-classification correction (same unmerged draft PR). Every
potentially related completion/settled audit row is enumerated from FIELD
PRESENCE before any closed typed comparison. A present recovery marker,
result-session or settled causal identity (attempt/candidate/envelope/
notification/generation/result/session) that matches the exact current
result/session OR is malformed (`null`/bool/int/list/string) makes the row
related; a non-object body is opaque and never ordinary absence; only a row
whose every present identity is well-typed and provably a DIFFERENT value is
independently unrelated. A recovery-shaped completion row is one carrying the
`_recovery_session_id` key OR the settlement-only `result_id`/`session_id` keys
(never the ordinary `CompletionReport` producer payload), so removing the
marker key cannot turn a settlement completion into ordinary absence while the
legitimate ordinary producer audit and the recovery-owned evidence still
coexist. Exact settlement and replay refuse read-only on a
malformed/conflicting/duplicate related row with zero Q/audit/allocation
change. The ordinary settlement branch no longer vetoes on the mere existence
of ANY root/manager receipt: a receipt blocks ordinary completion only when a
recovery/origin/accepted-result session or accepted-result identity matches the
current result/session or is malformed, or its state is nonterminal/unknown; an
unrelated ESTABLISHED TERMINAL receipt
(`callback_consumed`/`superseded`/`expired`/`restart_settled`) whose every
identity is well-typed and disjoint neither supplies current authority nor
vetoes ordinary completion and remains byte-for-byte unchanged. Explicit
recovery without an exact Q still fails `receipt_missing`/`identity_mismatch`;
no Q, audit, synthetic settlement or historical row is rewritten.

Checkpoint C3d3a lands the accepted R4 publish step-3 storage seams on the same
unmerged draft PR: callable, Database-owned authenticated notification discovery,
publication claim, acknowledgement, bounded queue-failure bookkeeping and
generation invalidation. It adds NO table and changes no released column or
overloaded meaning; it completes only this PR's existing unreleased N/D state
and evidence columns. Discovery
(`Database.list_authority_policy_v2_publication_targets`) is a READ-ONLY listing
of every `needed`/`publishing`/`published` notification whose current root
dispatch is `pending(G)`, independent of whether a recovery transition just
happened; it deliberately includes live-lease and exact already-consumed-receipt
rows because actual reclaim eligibility belongs at claim. The claim
(`Database.claim_authority_policy_v2_notification_publication`) refuses caller
transaction nesting with `transaction_owned` before it would `BEGIN`/`ROLLBACK`/
invalidate the owner and otherwise runs ONE synchronized `BEGIN IMMEDIATE` that
re-authenticates the complete post-final evidence, the actual Pending/null
`block_kind`/not-cancelled causal owner/session and the pointer G; it CASes
`needed -> publishing` or reclaims `publishing`/`published` ONLY after the bound
daemon-process identity differs from the persisted publisher boot (verified
death/restart) or the 30-second server-clock lease expired — never a caller
allow boolean and never elapsed time alone to declare a still-live publisher
dead — then increments the bounded positive publication attempt (overflow
refuses), records the bound boot/lease, keeps the canonical row/columns
consistent and appends exactly one closed `publish_claimed` audit for the exact
G/P in the SAME transaction. A failure restores the previous state/counter/lease
and appends nothing, and a live duplicate or wrong tuple is a bounded refusal
that never poisons the winner. The acknowledgement
(`Database.acknowledge_authority_policy_v2_notification_publication`) requires
the exact G/boot/P and state `publishing`, authenticates the retained closed
claim audit and the Pending causal owner, CASes to `published` with exactly one
closed `published` audit atomically and returns an exact read-only retry; a stale
P/boot never acknowledges or resets a newer claim, and an
`admitted`/`settled` notification whose consumer outran acknowledgement is never
returned to `published` (only a `publish_returned` observation for the exact
prior P would be eligible, and until the real generation-admission producer
exists that evidence cannot be authenticated, so the path refuses fail-closed
with `admission_evidence_missing` and zero mutation). The failure bookkeeping
(`Database.record_authority_policy_v2_notification_publication_failure`) keeps
the monotonic attempt number, clears the publisher lease so the notification
stays safely reclaimable and appends one closed `publish_failed` audit; if
recording fails the prior publishing lease/state survives reclaimable. The
invalidation
(`Database.invalidate_authority_policy_v2_notification_generation`) authenticates
the complete post-final evidence while allowing the root pointer to have
legitimately advanced to a replacement generation, atomically marks the exact
old G `invalidated` and retires D ONLY while it still names that G with one
closed `invalidated` audit; it never mutates a cancelled/terminal/replacement
task, retires replacement generation B, resets N to `needed`, spends E or
creates any escalation/notification-routing side effect, and a failed audit rolls
its own invalidation changes back. None of these writers performs a queue call.
`AuthorityPolicyStore` exposes thin forwarders (`list_v2_publication_targets`,
`claim_v2_notification_publication`, `acknowledge_v2_notification_publication`,
`record_v2_notification_publication_failure`,
`invalidate_v2_notification_generation`) that never begin/commit/rollback. The
prerequisite ordinary-evidence correction is included:
`_v2_completion_row_is_ordinary_related` now evaluates EVERY present causal
reference before declaring a completion row independently unrelated, so a
well-typed-but-DIFFERENT result id can no longer veto a surviving exact or
malformed session reference; the Cartesian relatedness matrix lives in
`tests/test_authority_v2_finalization_settlement.py` and the publication seams
in `tests/test_authority_v2_publication_bookkeeping.py`. Production automatic
publication and generation admission remain the NEXT unit: the real publisher
plus the non-bypassable generation-admission/fallback fences, envelope spend,
the startup/reaper/run-step discovery wiring and the editable-pair
editor/browser path remain unimplemented and these storage methods alone confer
no launch authority.

The C3d3a correction (same unmerged draft PR) hardens those publication seams
before any production publisher exists. Every publication writer first requires
a read-only SETTLEMENT PROOF: the real ordinary completion evidence for the
exact causal result/session (only while no potentially related recovery receipt
blocks it) OR the exact real `callback_consumed` Q for this root/agent/recovery
session plus BOTH complete settlement audits. The exact-recovery branch requires
the ONE potentially-related receipt to BE that exact Q, so a second related,
malformed or nonterminal receipt is a conflict that a single exact Q can never
hide, while an unrelated established-terminal receipt stays non-blocking; no
transaction-owning settlement method is called and no Q is transitioned or
fabricated. Reclaim, initial failure recording and the exact failure replay
authenticate the exact retained `publish_claimed` event and every state-required
`published`/`publish_failed` event BEFORE allocating a new attempt or clearing
the lease, so missing, mutated, duplicated or conflicting retained evidence
refuses with no repair-by-reinsertion and a null lease alone is never proof of a
genuine audited failure; a stale caller cannot poison the winning publisher.
Potentially related publication events are enumerated and classified BEFORE any
attempt/generation/P discriminator filtering: a duplicate whose `attempt_id` is
null/missing/mistyped or whose candidate/result/envelope/notification/generation
reference is wrong or malformed is a related conflict rather than an unrelated
row, an opaque non-object body is never ordinary absence, and a row whose every
present causal reference is well-typed and provably different remains
legitimately distinct prior `P` history. The retained history itself is
closed: a generation's `publish_claimed` set must be exactly the contiguous,
duplicate-free `{1..P}` authentic claim events, so an appended row whose only
defect is a well-typed impossible zero/negative/future `P` (or `P` plus a null
generation) refuses instead of masquerading as prior history, while a genuine
earlier attempt stays valid after a real reclaim. Invalidation now requires an affirmative
durable cause read from current state under the same owned transaction — a
cancelled task, a well-typed replacement owner/session, or a root pointer
displaced onto a replacement generation B — so a null/malformed current identity
field alone refuses, a healthy exact owner/pointer stays publishable and
unchanged, and only the exact old G may be retired. The publication-stage
shipping proof is extended in `tests/test_authority_v2_shipping.py`: the fresh
and full historical-migrated real owned-RuntimeDir/two-agent subprocess
CLI -> HTTP -> persisted-result venues drive finalized/exact-settled ->
discovery/claim/acknowledgement, with missing settlement and missing retained
claim refusals; the methods stay dark and the provider launch remains the sole
external-launch double.

Checkpoint C3d3b (same unmerged draft PR) adds the real publication entry and the
atomic generation admission plus its mandatory fallback fences. The publisher
`runtime.orchestrator.authority.publish_authority_policy_v2_notifications`
independently discovers every publishable generation, claims through the
authenticated C3d3a public seam, and ONLY after a winning claim calls the real
`TaskQueue.put_nowait(slug, root, metadata={"authority_v2_generation": G,
"publication_attempt": P})` OUTSIDE any DB transaction, then authenticates and
acknowledges the exact claim; a queue exception uses the bounded audited failure
path, a failed claim performs NO queue call, and `P` is diagnostic while only `G`
is admission authority. `Database.try_claim_v2_continuation_generation` is the
non-bypassable fence: one synchronized `BEGIN IMMEDIATE` re-authenticates the
complete post-final evidence, the settled final generation, the exact tagged token
G, D `pending(G)`, N `publishing` OR `published` (a consumer can outrun
acknowledgement), no prior reservation and the causal Pending owner; it atomically
writes N `admitted` + `next_session_id`, D `admitted(G)`, the task to
`in_progress` with a normal monotonic step increment and the reserved
owner/session, and exactly one closed `generation_claimed` audit, rolling every
field back on any failure. The separate
`Database.settle_v2_continuation_generation_admission` CASes `admitted` ->
`settled` with one closed `notification_settled` audit and grants no launch
authority; only the uninterrupted winning owner that received the reservation
launches with that exact runtime session, settlement audit failure holds dispatch,
and a reopened/replayed path settles exact evidence or reads it back but never
dispatches or repeats admission. Ordinary `Database.try_claim_for_step` now fences
a pending v2 root-dispatch pointer (a root cannot win on status/block_kind alone),
and `run_step` admits a tagged queue item ONLY through the generation claim: a
malformed/present-null/empty/stale/mismatched token refuses without any ordinary
fallback. The consumer-outruns-acknowledgement path now validates the COMPLETE
admitted/settled stage pre-state — the bounded contiguous `{1..P}` claim and
observation history, the exact G/D/next-session reservation and
`generation_claimed` (the ABSENCE of `notification_settled` while `admitted`,
and exactly the authentic `notification_settled` once `settled`) and the genuine
ordinary-or-exact-recovery settlement proof — and classifies every
possibly-related `publish_returned` observation three ways before any
attempt/G/P/boot filtering: zero authentic-related observations permits exactly
ONE insert after every prerequisite authenticates, one byte/type/closed-shape
-correct observation is a read-only exact replay, and any malformed, duplicate,
conflicting, opaque, extra-key or foreign-with-an-exact-reference observation
refuses with the exact prior residue. A false read is never permission to
append, and N/D/task are never regressed. The real
publication/admission venue is proven in `tests/test_authority_v2_shipping.py`
over both fresh and full historical-migrated owned RuntimeDirs through the ACTUAL
publisher -> real `TaskQueue` -> `Dispatcher`/`run_step` -> reserved-session
`_run_agent` -> held external launch, and
`tests/test_authority_v2_generation_admission.py` covers the atomic claim,
settlement, duplicate/two-connection contention, generation negatives, the
ordinary fence and the acknowledgement race. The automatic v2 authority
continuation hook stays fail-closed, envelope spend and the full
consumer/reaper/startup-recovery integration remain later units, and the feature
remains unaccepted.

The C3d3b correction (same unmerged draft PR) makes generation admission and
admission settlement enforce their COMPLETE row/evidence pre-state instead of one
sentinel per symptom. Admission additionally requires the complete retained
publication evidence for the exact generation — the bounded contiguous `{1..P}`
claim history, the bound `P`/publisher boot and every state-required
`published`/`publish_failed`/`publish_returned` observation, each a duplicate-free
closed event classified before any discriminator filtering — and proves the
ABSENCE of any prior related `generation_claimed`/`notification_settled` event; a
deleted, duplicated, preexisting, foreign, malformed, opaque or extra-key row
refuses with the exact prior residue and is never repaired by reinsertion.
Settlement reuses that retained publication evidence plus the genuine
ordinary-or-exact-recovery settlement proof at every boundary, requires coherent
absence of settlement-stage evidence before the first `admitted -> settled`
transition, treats an already-recorded claim/settled event inconsistent with the
current N/D as a conflict, and replays `already_settled_exact` read-only. Every
public writer still refuses caller transaction nesting before BEGIN/rollback.
Focused fault injection now targets the exact N/D/task UPDATE,
`generation_claimed`/`notification_settled` audit and commit boundaries —
including invalidation's LATER root-dispatch UPDATE and reclaim failures that
retain the previous P/boot/lease/claim/failure history — and the fresh AND full
historical-migrated shipping venues add missing-retained-claim and
missing-settlement-proof negatives that launch nothing, preserve the durable
residue and take no ordinary fallback. The admitted/settled acknowledgement
branch is covered by the same complete-pre-state rule (the omitted correction
closed by TASK-8555): a deleted ordinary completion, a preexisting
`notification_settled` while `admitted`, a conflicting current-P
`publish_failed`, and any conflicting/duplicate/malformed/null/wrong-type/
extra-key/opaque related `publish_returned` observation now refuse with the
exact admitted/settled residue and never fabricate an observation, while a
publish_returned audit-insert or commit failure rolls back for one safe exact
retry; the fresh AND full historical-migrated shipping venues add a
barrier-established (never elapsed-sleep) admission-before-corruption
acknowledgement negative that proves the refusal, the unchanged admitted
reservation/N/D and no continuation launch.

Checkpoint C3d3c1 (same unmerged draft PR) lands exactly the accepted R4
next-result spend and its result-keyed READY decision receipt, and nothing more.
`Database.spend_authority_policy_v2_continue_envelope` (thin store forwarder
`spend_v2_continue_envelope`) is ONE synchronized `BEGIN IMMEDIATE` writer that
refuses caller transaction nesting before BEGIN/rollback and otherwise
re-authenticates the complete post-final causal evidence, the exact tagged
generation `G` still named by `D`, the reserved same-root manager session, the
complete retained publication evidence plus the genuine ordinary-or-exact-
`callback_consumed` settlement proof, and the immutable spending result `R2`
(a real `task_results` row for the exact root/agent/reserved session, distinct
from the causal result, whose EXACT normalized report identity — the same
type/field-presence-preserving material projection the shipping callback
admission persists — is derived from the retained row and bound into the durable
`spent` receipt as `report_digest`, with its own authenticated v2 launch binding)
together with the separated proof that ZERO potentially-related `spent` events
exist. It atomically CASes the envelope
`active -> consumed` with `spending_result_id=R2` and `decision_state='ready'`,
the root dispatch `admitted(G) -> retired(G)` and exactly one closed `spent`
(`ax`) audit, retaining the task, `R2`, `N`, `K/P/V`, the causal `J` and every
prior audit byte-for-byte, with NO consumer call, task-status effect, child
creation or queue call. The consumed envelope carrying `spending_result_id` IS
the result-keyed spending receipt (partial UNIQUE index), and `decision_state`
is the closed forward-only `ready|claimed|applied|refused` receipt state; only
the spend-to-`ready` writer ships here and the ordinary decision consumer
(`ready -> claimed -> applied/refused`, the ready->claimed CAS plus decision
audits, and the common run_step/startup/reaper/shipping wiring) remains the NEXT
serial unit. An exact same-R2 retry authenticates the consumed/retired/ready
receipt read-only as `already_spent_exact` (including after a reopen) with no
remint or dispatch, re-deriving `R2`'s normalized report digest from the
persisted row so ANY material report-field drift (a syntactically valid changed
decision body or any summary/status/confidence/verdict/output-path/risks/
wait-ID/local-CI change) refuses; a causal-R replay, a foreign/wrong-session/
malformed/nonexistent `R2`, a missing/null/mistyped generation or session, a
replaced or cancelled owner, a conflicting/malformed/opaque/duplicate/extra-key
`spent` event, a spent-shaped related row whose stage discriminator is
absent/null/mistyped/unknown (a non-`spent` row is skipped ONLY when it is a
recognized other result stage carrying that stage's exact closed key set), a
distinct later `R2` receipt and a stale-`A`-after-replacement-`B` pointer all
refuse with the whole transaction rolled back (envelope active, dispatch
admitted, `R2` retained, decision unapplied). Every distinct envelope
UPDATE, dispatch UPDATE, audit-INSERT and COMMIT failure is proven to roll the
transaction back before one safe exact retry, and two real connections with a
deterministic `BEGIN IMMEDIATE` rendezvous prove ONE spend and ONE receipt/audit.
A later legitimate selector activation never invalidates the pinned generation,
and a post-spend late acknowledgement carrying the ACTUAL retained publication
attempt/boot refuses as bounded `stale_claim` while a post-spend invalidation is
exactly `not_invalidatable`, both with the complete durable residue preserved and
no pointer/task regression. The
method is deliberately DARK (no automatic caller) and produces no launch
authority; `tests/test_authority_v2_envelope_spend.py` drives the healthy,
replay, refusal, boundary, contention and post-spend matrix at the real
Database/store seam, and `tests/test_authority_v2_shipping.py` extends the fresh
AND full historical-migrated owned-RuntimeDir/two-agent subprocess CLI -> HTTP
venue through the actual reserved next invocation and real `R2` callback
admission to an explicit public spend invocation — stage proof with a direct
storage handoff; the C3d3c2 checkpoint above adds the real common-consumer path
end to end over the same venue (existing spend -> claim -> real normal decision
effect -> applied, plus a zero-entry failed-spend case, a post-effect
acknowledgement failure whose effect survives a reopen refusal, and the migrated
venue) — including a deterministic spend-audit
failure that preserves the retained `R2` and then succeeds on the exact retry,
plus labelled fixture-corruption A/B negatives at the actual spend boundary (a
null-discriminator related audit row and a changed `R2` report body over the
genuine shipping admission), each followed by a restored healthy exact replay.

The file-backed completion CLI preserves a supplied `manager_self_evaluation`
member verbatim (including invalid/null values) so the daemon, rather than the
client, validates it; an omitted member remains omitted. The shipping CLI to
real loopback HTTP to durable result path is exercised by
`tests/daemon/test_completion_cli_loopback.py` against a real uvicorn server
(not a mock or in-process ASGI client). One current v1 limitation is recorded
there: `CompletionBody.manager_self_evaluation` is `object | None`, so a
present JSON `null` reaches the route but is indistinguishable from an omitted
member and skips validation. The full v2 CLI -> durable result -> hook ->
Pending/enqueue lifecycle, launch-bound v2 authentication, legacy/new writer
convergence onto the selector, recovery/reaper and API/UI work remain
outstanding serial units; this staged checkpoint neither activates a policy nor
claims that lifecycle.

Checkpoint C3d3c2 (same unmerged draft PR) lands the ordinary decision-dispatch
claim/acknowledgement/interruption-refusal family and its directly coupled
common-consumer gate, and nothing more. The consumed envelope carrying
`spending_result_id` is the result-keyed decision receipt; its closed
forward-only `decision_state` is the durable single-use token.
`Database.claim_authority_policy_v2_decision_dispatch` (thin store forwarder
`claim_v2_decision_dispatch`) is ONE synchronized `BEGIN IMMEDIATE` writer that
refuses caller transaction nesting before BEGIN/rollback and otherwise
authenticates the COMPLETE spent receipt (the exact post-final `J/K/P/V/E/N/D`
and final audit set, the retired exact generation, the settled notification, the
complete retained publication evidence plus the genuine ordinary-or-exact-
`callback_consumed` settlement proof, both `generation_claimed`/
`notification_settled` admission events, `R2`'s own authenticated launch binding,
the exact `spent` audit whose bound `report_digest` still re-derives from the
persisted `R2` row, and the still-current nonterminal reserved invocation), proves
the ABSENCE of any potentially-related prior decision event, and CASes
`ready -> claimed` with exactly one closed `decision_claimed` audit. ONLY that
winning return authorizes exactly ONE ordinary consumer entry; a
duplicate/restarted `claimed`, an `applied`/`refused` receipt, a conflicting/
opaque/malformed/missing identity or a replaced/cancelled owner returns bounded
`decision_pending` and never authorizes the consumer, and a failed claim/audit
rolls back to `ready` for one exact retry without spending or evaluating again.
`Database.acknowledge_authority_policy_v2_decision_dispatch` re-authenticates the
complete spent receipt plus the single closed historical `decision_claimed` event
and CASes `claimed -> applied` with exactly one closed `decision_applied` audit;
it deliberately does NOT require, restore or regress the old task/owner/blocked
projection (the consumer's independently committed task/child/effect rows and any
generation `B` are preserved byte-for-byte), an exact applied replay is read-only
`already_applied_exact` and never a second consumer call, and a failure leaves the
receipt `claimed`. `Database.refuse_authority_policy_v2_decision_dispatch` is the
audited `decision_dispatch_interrupted` refusal for an exception/restart after a
committed claim (before the effect, after an independently committed task/child
effect, or after a failed acknowledgement): it preserves the spent envelope, the
causal `J/K/P/V` evidence and every already-committed effect, escalates ONLY the
still-current nonterminal reserved invocation (a cancelled/terminal/replaced task
and any generation `B` are preserved exactly, with no task mutation), retires/
settles only the exact owned obligation and never mutates a replacement pointer,
and CASes `claimed -> refused` with exactly one closed
`decision_dispatch_interrupted` audit; a failed refusal transaction rolls back
only itself, retaining the discoverable `claimed` state and a bounded pending
outcome. `Database.get_authority_policy_v2_decision_receipt_for_result` is the read-only
receipt lookup (`None` only when no consumed envelope carries that exact
spending result; a corrupt canonical body returns `{"corrupt": true}`), and
`Database.authority_policy_v2_decision_result_report_binds` normalizes BOTH sides
through the established shipping representation
(`completion_report_from_result_row`: the persisted `confidence_score` column is
the model `confidence` field and the persisted raw `decision_json` is parsed into
`NextStep` with the reserved carrier stripped), so the complete materially-consumed
decision (not merely its action) plus every summary/status/confidence/verdict/
output-path/risks/wait-ID/local-CI field must match the retained `R2` row while the
persisted full material digest stays separately bound into `spent`.
The COMMON consumer is guarded: `run_step.py` splits the
existing `_consume_completion_report` into the single guarded entry (used by
ordinary completion, accepted recovery, the startup sweep and the zombie reaper)
and the unchanged `_consume_completion_report_body` (the EXISTING normal decision
body). Before ANY normal task mutation/orchestration audit/decision/delegate/
enqueue effect the guard classifies the completion against the REAL persisted v2
lineage through `Database.authority_policy_v2_completion_dispatch_context` (never
reader absence or a mock's `None`): `no_v2` only when the root has no v2 lineage
at all (a pre-final, attempt/candidate-only lineage is unchanged ordinary);
`causal` (the causal result of a finalized generation) is continuation bookkeeping
only and never spends, remints or re-enters the normal effect; the exact active
reserved next result `R2` is atomically SPENT through the EXISTING writer and then
claimed exactly once; an exact `ready` receipt first requires the supplied report
to bind to the retained `R2` body before claiming; a restarted `claimed` receipt
performs audited interruption refusal; `applied`/`refused` are read-only; a fully
terminal generation classifies a completion as `later` (ordinary-capable) ONLY
when it is a GENUINE later result — the persisted result's agent/session are the
root task's current durable owner (`assigned_agent`) and session
(`current_session_id`), both non-empty, and the row is genuinely later than every
retained terminal v2 evidence row (causal result and spending result) — and the
common gate then additionally requires the supplied report to match that exact
result's persisted material identity before any ordinary effect; a bare/empty/
wrong-agent session, a wrong owner, a merely larger/latest row id, terminal flags
or an absent exact receipt are never sufficient and stay a fail-closed `foreign`
skip, while every retained consumed envelope's exact terminal decision evidence
(the retained `decision_claimed` plus `decision_applied`/`decision_dispatch_interrupted`
events and the re-derived `spent` receipt) must still authenticate so an older
live/corrupt generation behind a terminal pointer keeps the lineage live; an
identity with no real persisted result row on a root that HAS v2 history
(including `None`/bool/string/unknown integer) is a fail-closed `foreign` skip,
never the ordinary/v1 absence path; and every other unmatched/malformed/foreign
identity is a fail-closed skip, never ordinary permission, with a classification
read failure also fail-closed. The winning caller retains the exact CAUSAL receipt identity from
`AuthorityPolicyV2CompletionDispatchContext` and acknowledges/refuses with THAT
identity (never the reserved spending result id and never post-effect
task/owner/pointer state); a bounded pending writer outcome leaves the receipt
recoverable and never permits a second consumer. Every first-write/replay
authenticates the COMPLETE closed decision-event set, so a mutated preceding
`decision_claimed` refuses exactly like a mutated final event, and a historical
acknowledgement/refusal still settles generation A after the consumer legitimately
advanced the root pointer to generation B (A's retirement is proven by the exact
`spent` audit, not by a still-current `D`). An exception raised by the normal body
after the claim triggers the refusal bookkeeping and is re-raised, and an
acknowledgement/audit failure leaves the receipt `claimed` and never re-runs the
consumer. The accepted-recovery special branches classify against the same
persisted lineage before ANY special effect (including a read failure, which is
routed fail-closed) so they cannot bypass it: a `later` result routes through the
same guarded common entry (never a special branch). The automatic v2 authority
continuation hook stays fail-closed/DARK: the broader startup/reaper/queue
producer discovery and the editable-pair editor/browser path remain
unimplemented. `runtime/orchestrator/authority_policy_store.py` exposes thin
`claim_v2_decision_dispatch`/`acknowledge_v2_decision_dispatch`/
`refuse_v2_decision_dispatch`/`get_v2_decision_receipt_for_result`/
`v2_decision_result_report_binds` forwarders that never begin/commit/roll back and
perform no consumer/queue/external-process call.
`tests/test_authority_v2_decision_dispatch.py` drives the healthy claim/ack/
replay, interruption refusal, cancellation preservation, caller-nesting,
boundary-injection, two-connection one-winner and corrupt/malformed-prior-event
matrix, the genuine-later-result positive (real session publication plus real
callback admission) and its invalid/drift/None-resolver negatives at the real
Database/store seam; `tests/test_authority_v2_shipping.py` proves the ACTUAL
reopen over the SAME persisted owned-RuntimeDir database — a distinct `Database`/
connection object with the protected process context rebound, quiesced by
deterministic tagged-run_step/task_done barriers (never elapsed sleep) — for the
post-effect failed-ack interruption refusal, the preserved task/child effect, the
no-repeat consumer/child/enqueue count and the exact read-only replay over both
fresh and full historical-migrated venues.

The next serial checkpoint wires startup pre-final refusal discovery and the
zombie reaper's exact v2 completion effects without changing the existing v1 or
no-policy paths. `_sweep_on_startup` performs authenticated unfinalized-attempt
discovery before any per-task recovery branch. It re-reads every exact
housekeeping target and asks the existing refusal transaction to close an
old-boot or durably failed pre-final obligation using the closed code for the
greatest committed stage. The task root remains fenced from later startup
effects even when a same-boot live owner correctly refuses to yield or when the
refusal transaction fails. A malformed discovery fails task recovery closed;
final `continued` attempts are not pre-final work and stay with the existing
settlement/publication reconciliation. This seam never evaluates policy,
remints evidence, falls back to ordinary/v1 enqueue, or creates a successor.

For a persisted result, the zombie reaper first invokes the real guarded
`_consume_completion_report`. A v2 session then clears `zombie_flagged_at` only
through `Database.consume_v2_fingerprint_and_clear_zombie`: one
`BEGIN IMMEDIATE` transaction re-authenticates the exact task, assigned agent,
current session generation, immutable result identity, original non-null marker
and the unique allowed v2 consumption receipt. Any result, owner, session,
marker, cancellation, replacement or receipt mismatch is a zero-write denial;
an audit/commit failure rolls back the clear. For a result-absent TTL expiry,
`Database.cancel_zombie_without_fingerprint` uses one transaction to predicate
the exact owner/session/marker, `in_progress`, null block/cancellation state and
continued absence of an exact task/agent/session result, then atomically writes
the cancellation and audit. Parent wake happens only after that CAS commits.
Both reaper paths select v2 from the immutable session-policy binding rather
than the mutable active selector, and mixed/malformed binding evidence cannot
fall back to legacy mutation. Focused coverage lives in
`tests/test_authority_v2_startup_reaper.py`; the dual-text editor/browser,
complete parity sweep, main convergence and final review/QA/CI remain later
units.

Checkpoint C3d4a (same unmerged draft PR) adds the common DB-aware TASK enqueue
boundary and converges the direct producers onto it.
`Database.classify_authority_policy_v2_root_dispatch_for_enqueue` is a narrow
synchronized read that distinguishes a provably ABSENT root-dispatch pointer
(unchanged ordinary enqueue) from a live `pending(G)` generation (route through
the authenticated publisher), an `admitted` generation (never relaunched by an
ordinary enqueue), a `retired` pointer (legitimate later work stays ordinary),
and every malformed/present-corrupt or unreadable state (fail closed) — the
existing `get_authority_policy_v2_root_dispatch` collapses absent and malformed
into one `None`, which is never ordinary enqueue permission.
`runtime.orchestrator.authority.enqueue_task_generation_aware(orch, queue, slug,
task_id, *, metadata=None, ordinary_enqueue=None)` resolves the TARGET root's OWN durable generation at
production time and either ordinary-enqueues (absent/retired, preserving trigger
metadata such as `job_terminal`/`triggering_job_id`), publishes through the
existing `publish_authority_policy_v2_notifications(..., root_task_id=...)` seam
(real claim -> raw `TaskQueue.put_nowait` outside any transaction -> exact ack),
or refuses with ZERO queue calls (admitted/malformed/unreadable, a pending root
whose claim did not win, or a pending root with no publishable notification).
Request metadata can never manufacture or replace G, and a parent's G is never
copied onto a child/successor; a publication may repeat but generation admission
may not (the DB claim fence stays the non-bypassable backstop).
Only a genuine durable `Database` drives the classification (a mock/duck-typed
orchestrator is not permission to consult or bypass durable state and keeps
the unchanged ordinary path), and each converged producer preserves its EXACT
original ordinary call shape through the optional `ordinary_enqueue` callable
(the direct producers' `put_nowait(slug, task_id)`, the blocked-job resume's
`enqueue(..., metadata=...)` and the runner/startup `enqueue`), so queue/drain/
dispatcher forwarding and `task_done` accounting are unchanged.
`runtime.daemon.runner.enqueue_task` is that boundary, and the direct producers
converge through it: the `run_step` successor/self-correction/delegate/feedback/
chain/parent-wake/job-unblock/fanout sites, the `authority` v1 ordinary
continuation producer, the `tasks` route successor/continue sites and the
`__main__` startup sweep ordinary/parked enqueues. The raw `TaskQueue.put_nowait`
remains the transport (including the authenticated publisher's own call and the
separate dream/schedule/wake queues, which are different domains), and the
run-step/DB claim remains the non-bypassable backstop. Accepted R4 also requires
the DEQUEUE side to request independent discovery/publication: when `run_step`'s
UNTAGGED ordinary `Database.try_claim_for_step` refuses because the target root
has a live `pending(G)` pointer, `run_step_impl` calls the EXISTING
`publish_authority_policy_v2_notifications(orch, queue, root_task_id=...)`
publisher and returns WITHOUT admitting or launching anything — it never adopts
G, never rewrites the stale/untagged metadata and never falls back to an
ordinary launch. Only that specific pending classification requests publication
(a genuine ordinary CAS loser, or absent/admitted/retired/malformed/unreadable
state, requests NOTHING and can never become a duplicate ordinary enqueue
through a blindly invoked common producer), and only a genuine durable
`Database` drives it. `tests/test_task_enqueue_boundary.py` covers the
classification and every boundary outcome against the real Database/publisher
seams, and adds Part C interleavings over a SECOND genuine `Database`
connection on the same file: cancellation and authenticated replacement-
generation-B pointer advance between producer classification and the
publication claim both refuse with ZERO queue calls, plus two loaded orgs with
the SAME textual task id resolved through the real runner entry/queues into
their OWN Database/generation/queue (one org's generation can never choose or
publish the other's target, and the idle-runner contract still refuses before
any queue call). `tests/test_authority_v2_generation_admission.py` adds the real
dequeue seam: a raw untagged pending item requests publication while a
separately consumed fresh TAGGED item admits and launches exactly once, and a
delayed tagged generation A consumed after replacement B is pending refuses at
the tagged admission fence. `tests/test_authority_v2_shipping.py` adds the
delegate+failed-acknowledgement branch over both fresh and full
historical-migrated venues with actual enqueue/body-entry counters that must not
increase across reopen/refusal/replay, and the REAL post-terminal LATER
lifecycle over both venues, continuing past the child/root-relaunch precondition
into the actual relaunched ROOT: a real manager R3 subprocess CLI -> HTTP ->
persisted root result -> the REAL common consumer's terminal-v2 `later`
classification (asserted from the real classifier BEFORE any effect) -> the
existing normal body (`done`) exactly once, with no manual owner/session/status
patch, unchanged generation-A evidence and no extra enqueue; the changed-supplied
-report report-binding refusal (zero normal-body/task/child/queue effect) is
asserted at the real gate, distinct from the route's own duplicate suppression,
and actual-ROOT unbound/wrong-agent/wrong-session/stale negatives are included.
The full startup/accepted-recovery attempt/receipt/refusal discovery, reaper
integration and automatic authority-hook orchestration remain the NEXT unit; the
hook stays fail-closed/DARK and the editable-pair editor/browser path remains
unimplemented.

Checkpoint C3d4b (same unmerged draft PR) wires the accepted POST-FINAL
receipt-settlement and pending-generation publication bookkeeping into the
ACTUAL recovery and startup seams.
`runtime.orchestrator.authority.reconcile_authority_policy_v2_post_final(orch,
*, root_task_id)` derives the immutable finalized envelope (E) and, through the
new read-only
`Database.get_authority_policy_v2_settlement_receipt_identity` (converged on the
exact immutable E/R/session identity by the C3d4b discovery correction below),
the exact real
recovery receipt Q, then settles through the EXISTING public
`settle_authority_policy_v2_continuation_receipt` writer (genuine recovery when
an exact Q exists, the ordinary branch requiring real completion evidence
otherwise, and read-only authentication for an already-`callback_consumed` Q)
and independently discovers/publishes every `needed`/`publishing`/`published`
`pending(G)` notification through the EXISTING authenticated publisher. It
evaluates, remints, spends and launches nothing and never runs the ordinary
decision body; a missing or conflicting settlement proof refuses with the prior
residue and produces no queue call. `_consume_accepted_completion_recovery`
routes a `causal` classification to that helper instead of the common
consumer's skip, and `runtime.daemon.__main__._build_state` binds the REAL
owning-process identity (`OrgState.authority_v2_origin_boot_id`, via
`OrgState.bind_authority_v2_owner`) and the existing server-owned
`_strict_permission_surface_digest` reader before recovery, then runs a
startup-wide publication pass with `limit=None` so every eligible root is
covered (no first-32 starvation). A same-boot unexpired lease is never stolen,
an already admitted generation is never republished/reclaimed, and the tagged
generation claim at dequeue remains the only admission. The pre-final automatic
authority hook, refusal/reaper orchestration and the editable-pair
editor/browser path remain LATER units; the hook stays fail-closed/DARK and the
dual-text feature remains unaccepted.

C3d4b correction (same unmerged draft PR). The post-final outcome is
AUTHENTICATED, never inferred from the absence of a refusal. The earlier helper
treated an already-`callback_consumed` receipt as reconciled whenever the
publisher produced no refusal (`settled = not refused`), so a deleted
notification (N), a deleted root-dispatch pointer (D) or a deleted
`generation_claimed` claim audit — each of which makes discovery legitimately
empty — was silently reported as a successful reconciliation. Reconciliation now
returns `reconciled` ONLY on a real writer outcome (`settled` /
`already_settled_exact` from the EXISTING
`settle_authority_policy_v2_continuation_receipt` /
`settle_v2_continuation_generation_admission` writers) or on an ACTUAL
authenticated publication of the EXACT generation (the publisher's own claim
re-authenticates the complete settlement evidence). An empty discovery, a
`discovery_failed`, a hidden malformed target, a refusal and a live lease are all
`settlement_refused` with zero queue calls and byte-unchanged durable residue; a
missing N, missing D or corrupt claimed/settled generation therefore refuses
instead of reconciling, and discovery remains discovery. An authentic generation
already ADMITTED by a real consumer (N `admitted`, D `admitted(G)`) is completed
through the EXISTING `Database.settle_v2_continuation_generation_admission`
writer using the immutable G and reserved session derived from durable rows, so a
reopened owner finishes the exact R4 step6 `notification_settled` bookkeeping
WITHOUT republishing or reclaiming the admitted generation, performing a second
admission, regressing task status/session/step or gaining launch authority; a
corrupt admitted/settled generation refuses unchanged. Actual success where the
exact settlement committed but the external queue failed is preserved (the
writer outcome is already `settled`/`already_settled_exact`), and is now
distinguished from settlement that was never authenticated.

`tests/test_authority_v2_post_final_reconciliation.py` drives the REAL
`_consume_accepted_completion_recovery` and `_sweep_on_startup` callers across
accepted/already-consumed/ordinary settlement, missing/conflicting-proof
refusal, lost-queue/new-boot republication, same-boot live-lease, and a real
queue-failure retry; `tests/test_task_enqueue_boundary.py` finishes the two-org
proof by draining both same-id orgs through the real
`Dispatcher.run_step` (tagged generation admission vs ordinary claim, duplicate
replay, named-org isolation), and `tests/test_authority_v2_shipping.py` adds the
same accepted-recovery seam over both fresh and full historical-migrated owned
RuntimeDir venues with a corrupt-settlement refusal.

C3d4b correction B (same unmerged draft PR). Publication is now PER-TARGET
resilient so one bad target can never starve the unrelated eligible roots
discovered later in the same startup pass. A raised claim, queue or
acknowledgement failure is caught at the publisher loop, converted into a
bounded receipt (`publication_claim_failed`, `publish_failed`,
`publication_acknowledgement_failed`) and the pass continues; previously a single
raised exception aborted `publish_authority_policy_v2_notifications` and every
later root went unpublished until the next boot (`_publish_v2_generations_on_startup`
only logged the top-level failure). The claim/ack transactions own their own
rollback, so a failed claim still performs ZERO queue calls, a queue failure keeps
the bounded audited failure record and the prior committed settlement, and an
acknowledgement failure leaves the raw tagged put done, the lease reclaimable and
the non-bypassable generation fence admitting exactly once. A bounded refusal is
still never permission: `reconcile_authority_policy_v2_post_final` requires a real
writer settlement or an actual authenticated publication of the exact generation,
so a per-target failure cannot become a false `reconciled` result or ordinary
decision/evaluation/spend/remint authority.

`tests/test_authority_v2_post_final_reconciliation.py` also injects receipt
settlement, publication claim, raw queue put, publication acknowledgement and
generation-admission settlement failures at their real couplings; covers
cancellation, affirmative owner/session loss and a malformed admitted reservation
identity as read-only refusals; proves a genuinely produced generation B
progresses on its own path while old A's committed generation is byte-unchanged;
proves a reopened, DISTINCT `Database` + real `OrgState` over the SAME persisted
file (with `bind_authority_v2_owner`'s server-owned permission-digest binding)
rediscovers a lost in-memory queue; and seeds 34 genuinely finalized roots so the
real startup publisher delivers every eligible root past the first 32 while an
early same-boot live lease is refused.

C3d4b discovery correction (same unmerged draft PR).
`Database.get_authority_policy_v2_settlement_receipt_identity` no longer treats
`len(receipts) > 1` as a blanket conflict before it classifies causal
relatedness. Given the caller's exact immutable envelope identity
(`manager_session_id` / `result_id`), it reuses the SAME potentially-related
classification as the ordinary settlement branch (`_v2_receipt_blocks_ordinary`):
exactly one potentially-related receipt that IS the exact current recovery
identity (`recovery_session_id`/`accepted_result_session_id` equal the current
manager session and `accepted_result_id` equals the current result) is returned;
zero potentially-related receipts means a genuine unrelated ESTABLISHED TERMINAL
historical receipt, which is neither authority nor a veto, so the ordinary branch
proceeds; and any other shape — a second potentially-related receipt, or one
related partial/malformed/nonterminal/unknown-state receipt that is not the exact
current recovery identity — is a bounded conflict. An unrelated established
terminal historical Q therefore no longer blocks a healthy current
`callback_accepted`/`callback_consumed` Q or genuine ordinary evidence, while a
related or conflicting Q can never disappear behind an unrelated sibling, a
session/result filter or the ordinary branch. The reader stays read-only (it
performs no transition and grants no authority) and the public settlement writer
still independently re-reads and authenticates the complete evidence; no new
evaluator/authenticator, table, column or dependency is introduced.
`runtime/orchestrator/authority_policy_store.get_v2_settlement_receipt_identity`
and `reconcile_authority_policy_v2_post_final` thread the exact identity through
unchanged. `tests/test_authority_v2_post_final_reconciliation.py` proves the
healthy current accepted and consumed Q plus both synthetic and genuinely
public-produced historical history (`_drive_realistic_terminal_history`), the
ordinary-completion-plus-history control, the genuine-current-Q-beside-ordinary
case and the related/partial/malformed/nonterminal conflict refusals, all at the
real recovery and startup callers.

C3d4b caller/reopen completion (same unmerged draft PR). The same test file now
drives the receipt-settlement, publication-claim, raw-put, publication-
acknowledgement and generation-admission-settlement failures through the ACTUAL
`_consume_accepted_completion_recovery`, `_publish_v2_generations_on_startup` and
`_sweep_on_startup` callers (the direct-publisher analogues remain labelled
publisher-unit tests), asserting each injection fired, counting RAW queue attempts
separately from ACCEPTED enqueues, and covering the new branch where a raw put
fails AND the failure-bookkeeping write raises (the prior `publishing` claim/lease
stays retained and the pass still publishes every later eligible root). It
extends the genuine reopen proof to admitted-but-unsettled settlement and settled
read-only replay over a NEW `Database` AND real `OrgState` on the same persisted
file (server-owned boot + permission binding), plus a corrupt-evidence refusal
with preserved residue; adds an actual old-A late receipt/admission bookkeeping
replay while authentic public-produced B is current (returned read-only
`identity_mismatch`, with B, the task/session/step and all retained A evidence
byte-identical) before B progresses on its own path; and runs the production
startup entry over a real `OrgState` with >32 eligible finalized roots, an early
authentic live lease, exact per-root generation isolation (per-root put counts,
not set size) and a real ordinary-root control. The `limit=None` plumbing test
stays a plumbing test. Genuine recovered subprocess CLI Part C and multi-org
`DaemonState` Part D remain the following serial work; the feature is not
complete and the dual-text feature remains unaccepted.

## Inline Delegation Chains

A manager can declare a multi-leg workflow in one `delegate` decision using `NextStep.then` and optional per-leg `expect_verdict` gates. The orchestrator auto-advances to the next leg when a child terminates completed with a matching verdict. Since THR-211, auto-advance may also fire from a child whose completion report has durably landed while its task row still reads `in_progress` (the completion-status-lag window) — the recognition is session-safe and at-most-once, and the chain gate consumes the exact authenticated `(task_id, assigned_agent, current_session_id)` report so a newer unrelated row can never advance or clear the chain; see `tests/test_thr211_completion_status_lag.py` for the session-bound regression cases.

A configured reviewer leg (org setting `reviewer_agents`, default `code_reviewer`) MUST declare `expect_verdict: "APPROVE"`; omission is a HARD REJECT — the whole delegation is denied before any child spawns, the owner receives a feedback task result and feedback orchestration step naming the required `expect_verdict: "APPROVE"`, and the root stays PENDING and is re-enqueued for a corrected decision (never a root failure). Missing agent / missing workspace delegates keep hard terminal failure. Same semantics apply to pipeline-carrier legs in fan-out decisions.

Implementation: `runtime/orchestrator/chain.py` and `runtime/orchestrator/run_step.py`. Spec: `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`.

Example:

```json
{
  "action": "delegate",
  "agent": "dev_agent",
  "prompt": "Build the feature...",
  "then": [
    {"agent": "senior_dev", "prompt": "Code-review the PR.", "expect_verdict": "APPROVE"},
    {"agent": "qa_engineer", "prompt": "QA the PR.", "expect_verdict": "PASS"}
  ]
}
```

## PR CI Wait / Guarded Merge

Merge-scoped engineering tasks are complete only after the PR is merged. PR creation, review APPROVE, or QA PASS alone are not terminal completion.

Use the first-class PR CI helper to submit a bounded job pinned to the PR head SHA, then block with `waiting_on_job_ids`. On resume, complete only if the job's structured verdict proves the PR was merged at that SHA. If the helper reports stale head, timeout, CI failure, missing checks, non-clean mergeability, or merge failure, re-ground instead of marking done.

The helper must enforce: review APPROVE, QA PASS, CI PASS, unchanged head SHA, mergeable CLEAN, open/non-draft PR, configured merge method. It must not rely on `gh pr merge --auto` when branch protection lacks required checks.

## Task/Subtask Terminology

The data model uses `task_type` `Literal['task','subtask']`:

- **Task** (`task_type='task'`): the task owner — holds the decision-making
  loop and produces `decision` blocks (`delegate`/`fanout`/`done`/`escalate`; the `parallel` alias is accepted for `fanout`).
- **Subtask** (`task_type='subtask'`): the delegated agent — executes a
  bounded unit of work and reports a plain completion (no `decision` field).

Prose in docstrings, comments, and prompt strings prefers "task owner" and
"subtask agent" over the legacy "team manager" / "worker" language. The
`task_type` enum values were already correct before TASK-573; the sweep only
updated prose, not schema or role-identity strings.

## Bounded Failure-Recovery (TASK-573)

When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior where
any subtask FAILED unconditionally cascade-failed the parent without giving the
task owner a chance to re-ground.

Contract (founder-approved in THR-028, refined in THR-078):

1. **Bounded wake.** On child failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason is available so the task owner can
   author an updated brief.

2. **Mechanical retry provenance (THR-078).** A manager may re-dispatch
   unchanged work or direct revised work with a valid `revisit_of_task_id`
   link to a FAILED same-parent predecessor. The link records history; it
   neither compares briefs nor itself authorizes root escalation. A later
   COMPLETED or SUPERSEDED descendant retires earlier FAILED ancestors from
   causal selection (THR-183).

3. **Manager ownership after failure.** An unresolved failed child keeps its
   durable lineage and wakes its owning parent for a manager decision. The
   runtime neither commits `runtime_retry_ceiling` escalation nor fails a
   nested decision owner upward. A manager-proposed escalation uses the
   existing THR-181 hook and its configured outcome; committed escalations
   remain human-resolved.

4. **Chain-leg failure.** A failed workflow chain leg clears the active chain
   and returns its decision owner to bounded wake. A passive fan-out pipeline
   carrier instead fails closed and preserves its causal leaf for the outer
   fan-out barrier; a fanout-dispatched `task` manager remains its local owner.

5. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

6. **Reviewer/QA verdict discipline.** A review/QA leg completes with an
   APPROVE/REVISE/PASS/FAIL verdict and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Traps:

- A linked historical failure is causal context, not a runtime escalation
  trigger: the owner decides unchanged re-execution, revised recovery, or a
  THR-181 escalation proposal.
- Retry links are mechanical provenance, never a semantic brief comparison:
  unchanged assignment re-execution and manager-directed revised work both
  require an explicit valid predecessor link, and the daemon creates neither
  retry nor successor loops.
- Chain-advance in `_enqueue_parent_if_waiting` handles FAILED subtasks:
  failed chain legs clear the chain and fall through to bounded-wake.
- Self-block (`status=blocked` + empty `waiting_on_job_ids`) is a malformed
  report that fails the review/QA leg. Never self-block in a review/QA role.

Inline traps:

- Auto-advances do not consume orchestration steps. Declaring a chain costs one step; the final-leg wake costs one.
- A final-leg match still wakes the manager. Chains never auto-`done`.
- Cross-team validation runs on every leg at parse time. An off-team agent on any leg rejects the whole decision.
- Do not pre-embed upstream context in a leg prompt; `build_prior_leg_context` appends it automatically.

## Daemon-Restart Sweep (THR-064)

On daemon restart, `_sweep_on_startup` recovers tasks that were killed mid-flight.
Before Branch 1, a claimed but unaccepted THR-247 completion recovery is
settled fail-closed in its dedicated ledger transaction when its durable
origin or recovery binding is still current. This spends the episode,
terminalizes the task, and invokes ordinary owned-job `task_ended` cleanup;
it never launches a second recovery or uses a persisted PID as containment.
An accepted callback, cancellation, or newer binding wins unchanged. Accepted consumed
receipts settle only their captured running jobs. The bounded parent/chain effect is
rechecked under that same owner predicate; any thread followup is deliberately after
the guarded handoff and can be superseded by a later replacement. A manager
DONE whose terminal owner CAS committed before its recovery marker is
reconciled only for the exact task/agent/runtime-session/result receipt; its
post-commit cleanup and delivery are not part of that terminal transaction, so
a competing completed owner receives no stale recovery effects. The live
120-second deadline ends at callback admission, not descendant work,
settlement, or cleanup. This is a partial purpose gate: it makes no deployment
claim and retains the accepted bearer/sessionless/shell residual powers. If an
accepted root-manager escalation is instead committed by the existing
authority hook as `CONTINUE_SAME_ROOT`, the completion receipt is reconciled
only from that committed causal result/candidate/envelope tuple and current
owner; restart never reruns the evaluator or guesses from `pending` alone.

Receipt-owned cleanup tails and startup Branch 2 can reach the same parent
wake concurrently. They (and the already-bounded blocked-job startup producer)
therefore use `TaskQueue.enqueue_if_absent`: the queue owns one thread-safe
per-task reservation claimed under its lock; generation-aware and DB-aware
publication then runs with that lock released. A concurrent bounded producer
loses to either the reservation or an actual pending item. The reservation is
cleared on publication success, refusal, exception, or cancellation, so a
refused publication cannot strand future wake ownership. Callers never inspect
`asyncio.Queue`'s private deque. Suppression is pending-only; removing the item
for execution atomically clears the pending count, so a later valid transition
can publish another wake. Unrelated entries retain FIFO order and ordinary
enqueue remains deliberately non-coalescing. Tests must join every cleanup
worker they create before another sweep, inspecting the queue, or restoring
shared `jobs_runner` state; durable job settlement alone is not cleanup-tail
completion.

Branch 1 (in_progress + block_kind IS NULL — a live subprocess killed by the restart):

1. **Mark failed with restart context.** The killed child's note is enriched to
   `"daemon_restart -- infra fault, not a code failure; status-assess the
   branch/PR/CI and adopt already-pushed work before re-dispatching"`. This
   note surfaces to the parent manager via `_build_prior_steps_from_db`
   (as `result_summary`), so the manager can ground its next decision on the
   failure cause rather than treating it as a code bug.

2. **Parked-ancestor recovery.** The sweep marks killed children FAILED. If a
   killed child has a parked non-terminal ancestor
   (`in_progress` + `block_kind` in `{DELEGATED, BLOCKED_ON_JOB}`),
   bounded-parent recovery wakes the ancestor directly — no duplicate root
   is created. This is the same bounded-wake path used for any child failure.

3. **No auto-revisit (THR-079 + TASK-3604).** Startup recovery does NOT spawn
   an auto-revisit successor — the THR-079 ruling superseded the earlier
   heartbeat/revisit approach, and TASK-3604 removed automatic successor
   creation entirely from the run_step path. Dead in_progress tasks are
   fail-closed; the founder receives a `daemon_restart_failure` audit row
   and decides whether to re-dispatch.

4. **Fan-out barrier preserved.** A restart-killed child among still-live
   siblings does NOT wake the parked root early — only marking the killed
   child FAILED without enqueuing the parent. The existing N-wide all-children-
   terminal barrier in `_enqueue_parent_if_waiting` resolves when all legs
   report. The restart note survives to that eventual wake.
