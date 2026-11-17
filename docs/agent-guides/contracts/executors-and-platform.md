# Executors, skills and host capacity contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

**Optional reasoning effort (THR298 seq15/46).** AgentDef frontmatter owns `reasoning_effort` beside `model`. Unset adds no CLI effort override and no workflow agent-projection key. Built-ins accept case-preserving fullmatch `[A-Za-z][A-Za-z0-9_-]{0,31}` through shared `model_effort_args` validation; historical catalog values are metadata, not a membership gate. UI uses plain text: blank on explicit Save maps to null/default, without trimming or truncation. Explicit wire/direct empty strings refuse; CLI clear retains its existing meaning. Custom adapters still require approved declared-value membership (at most16 lowercase identifiers); legacy/model-only effort controls remain disabled with saved values visible. Token acceptance proves syntax/transport only, not native model/account support. With explicit effort, OpenCode requires nonempty provider/model slash components (additional model path segments allowed), no whitespace/control/NUL, at most one # and a nonempty embedded variant byte-equal to effort; it composes one literal `-m provider/model#effort`. Unset preserves the original model transport. Pi recognizes only its existing finite thinking suffix set; arbitrary safe colon suffixes do not become native thinking syntax. Codex keeps one literal quoted config argv element. CLI changes clear omitted model/effort, unrelated writes preserve them, explicit null clears. Custom adapters retain input-v1 `model:null` unless an ordinary exact-artifact update declares input-v2. Bounded evidence does not verify dynamic manage_agent update/enrollment at runtime (source only), native models/accounts, full task/thread/dream/wake/schedule lifecycles, host/callback or custom contained-wrapper prelaunch. General integration including callback is SKIPPED THR243seq42; former Python units are RETIRED. No ad hoc Product E2E or protected mutation/repetition proof is authorized. See [current contract](../../specs/2026-10-11-agent-reasoning-effort.md) and the four agent guides. `CLAUDE.md` remains the raw symlink to this canonical file.

## Architecture: Canonical Skill Store + Workspace Symlinks (macOS + Linux)

Skill delivery uses a **canonical skill store** — hash-addressed packages outside executor workspaces — with **workspace symlinks** to exact approved package versions under both `.claude/skills` and `.agents/skills`. The legacy per-session wholesale copy is permanently removed. The executor runs under the same OS identity as the daemon — linked, validated relative skill links live under BOTH `.claude/skills` and `.agents/skills`; every user-facing and executor-facing guidance surface names both roots, never only the provider-selected root. Guidance is operational, not a technical security boundary.

- **Platform:** macOS (darwin) and Linux. Windows and unknown platforms explicitly fail closed.
- **Delivery model:** The executor and daemon share the same OS identity. There is NO OS-level isolation. A same-UID process may mutate, race validation, and affect active/overlapping sessions. Integrity checks are DETECTION-ONLY with FAIL-CLOSED refusal — do NOT claim immutable, protected, read-only, OS-enforced isolation, or automatic repair.
- **Integrity verification:** Before each launch the daemon validates every resolved package member's bytes against the ledger-declared SHA-256 hashes. Pre-launch and retry-time manifest/member-hash plus both-root link validation occurs at real Popen/run seams. On mismatch the daemon emits a durable visible integrity event and refuses the session before Popen/retry. A mismatched existing canonical package is NEVER automatically rebuilt, copied, replaced, or healed from same-UID local source. First-ever materialization of an absent package remains allowed; valid existing packages may be reused. Recovery is manual, operator-invoked only: `happyranch skills recover <slug> <version> <content_hash>` (accepts the eligible current numeric B2 version ID, `system` for a current system contract, or the current release-catalog version; validates B2 artifact provenance or the current bundled release-source tree hash, audits before deletion, and refuses absent, valid or aliased release targets). This command requires a preceding authoritative external re-sync/redeploy of release or custom artifacts. It removes only the selected corrupted package; next materialization rebuilds from ArtifactStore for B2 or bundled release source for system/release skills. No automatic repair from same-UID local source. `set-executor` may repair links only after byte integrity passes — it never repairs bytes. Policy withdrawal and atomic link repair remain safe.
- **Session union:** All contexts (task, thread, wake, dream, schedule, bootstrap, executor-switch) use one fail-closed canonical verify/refuse boundary before launch. System-contract links are unioned across all ordinary session contexts so a later single-context launch never withdraws a valid link belonging to another context; release-managed and B2 custom-skill links remain policy-reconciled and withdrawable.
- **Retired System Assistant:** no feature routes, CLI, dock, bootstrap or config reader/writer. Preserve inert legacy files/links and historical records; keep the reserved `system_assistant` migration exclusion.
- **Custom-skill logical purge:** A founder may permanently tombstone only an already-retired current-v2 custom skill after exact-slug confirmation. Tombstones reserve ID/slug and permanently deny resolution, restore, mutation, and serving while retaining all versions, evidence, artifacts, canonical packages, and historical links. The default custom catalog excludes tombstones; explicit `view=removed` lists only tombstones, and direct detail receipts remain reachable. Recreating a tombstoned slug returns `409 slug_permanently_reserved`. Current eligibility rules are withdrawn by supersession, retaining their historical rows and making the preserved old resolver return `no_eligibility_policy`. At the unified canonical publication seam, one narrow per-org barrier spans the final authoritative tombstone/current-version re-read and both provider-root repairs; a purge committed during selection/build therefore excludes that stale spec from the returned set and both roots. Candidate construction and launch remain outside: there is no launch fence or running-session revocation, already published/running work is not recalled, no data is physically erased, and subsequent materialization remains fail-closed. Because `retired_at` remains set, downgrade alone cannot resurrect a tombstone; the deliberate pre-purge-binary limitation requires both operator downgrade and explicit restore of that exact skill, after which the policy-withdrawal latch still denies serving it.
- **Legacy fallback:** Permanently documented but cannot activate — link validation/repair, unsupported OS, or launch fail without catch-and-copy.
- **Residual risk:** Same-UID TOCTOU, active, and overlapping-session residual risk is accurately noted. Do not describe byte targets, local sources, ArtifactStore, or links as OS-immutable, ACL-protected, trusted, executor-only writable/unwritable, or automatically recovered.
- **Serving deployment** is independently verified after merge.

Detailed contracts: `docs/agent-guides/agent-executors-and-permissions.md` § "Canonical skill store + workspace symlinks", `docs/agent-guides/orchestrator-contracts.md`, `docs/agent-guides/agent-executors-and-permissions.md`.

Canonical package hashing, source copying and pre-launch tree verification
exclude only `__pycache__` path components and `.pyc`/`.pyo` filename suffixes.
Undeclared bytecode extras also do not invalidate B2 manifest reuse; declared
member hashes remain checked. Executor environments always set
`PYTHONDONTWRITEBYTECODE=1`. Clean-tree identities retain the previous hash;
source trees whose old identity included bytecode select a new path on the next
materialization, leaving old packages untouched. See the executor guide's
"Bundled skill sources and canonical delivery" section for upgrade details.

- **THR-207 capacity:** restart-required daemon defaults are 6 task workers and a healthy enforcement-capable global admission cap of 13 (6 task + 4 thread + dream + wake + schedule); macOS/no-enforcement remains conservatively capped at 4. The paired rollback is 4/11 plus restart.
- **Host-resource concurrency (THR-207 / TASK-5584 Slice A + TASK-5637 Slice B):** load-bearing ordering invariants remain governing: no agent subprocess launches before a daemon-wide admission lease; every terminal path (success, nonzero, timeout, cancellation, retry, shutdown, and setup failure) finishes containment and reconciles residue before releasing that lease; queued cancellation never launches and active cancellation uses the opaque containment handle rather than a bare PID. Ownership transfers at admission grant, carries the durable first-wins terminal reason, is generation-owned by session id, and is drained through the same registry, so an old generation cannot clear a newer PID/control and shutdown cannot lose an admitted attempt. The real task producer and both executor launch bodies use the capability backend; `SessionTracker` PID data is diagnostic/restart evidence while cancellation/cleanup controls are authoritative. Quiescence is fail closed: unreadable or errored containment evidence is unknown, not clean; Linux receipts retain truthful per-counter provenance and the process-group backend retains honest capped residuals. Under THR307seq117, the default factory wires that backend’s existing 1s sampler; per-attempt authenticated identity history is merged with fresh terminal observations, and each group signal requires a matching original or previously authenticated live same-group witness. Checked unreadable/partial/capped observations retain uncertainty and close the measurement gate; sampling settles before terminal evidence, and finish explicitly waits the direct Popen even when the bind gate skips communication. Concurrent run/cancel finalizers share one accounting/publication attempt: a different thread waits until that attempt settles, including contained publisher Exception handling, before terminal/release; same-thread claimed callback reentry remains a no-op. Callbacks run outside the attempt condition; every owner exit settles/notifies without retrying callbacks or copying the owner’s error to waiters. Internal bounds are 1024 trusted identities, 4096 rows per observation and a cooperative 1s observation allowance, with no silent eviction or hard wall/RSS guarantee. Known escaped survivors remain visible; unobserved between-sample escapes, arbitrary escaped-group cleanup, grandchild reaping, daemon-crash cleanup and historical launch/ownership attribution remain unproven. The healthy Linux backend applies and verifies the fixed resource limits while unsupported/degraded platforms state unavailable enforcement honestly. Bounded receipt provenance is published only through the existing operator surfaces. Governing implementation and the full platform-specific contract remain `runtime/orchestrator/host_supervisor.py`, `runtime/platform/session_backend.py`, and `docs/specs/2026-08-24-host-resource-concurrency.md`.
- **THR-247 bounded Codex completion recovery:** the one-shot task recovery passes the existing host supervisor's per-invocation `allow_retries=False` control. A recovery 429 is therefore finalized, receipted, cleaned up, and released once with its honest `RATE_LIMITED` result — it performs no supervisor backoff or re-admission. The default remains retry-enabled for ordinary task/thread/schedule/wake/dream invocations, except that a task with an explicit model or reasoning-effort override on a custom executor has no throttle/supervisor retry (THR-356); global retry counts, schedule, admission policy, cancellation reasons, and result diagnostics are unchanged. The honest-passthrough fallback also passes its existing empty executor throttle backoff so there is still only one provider execution. Accepted consumed receipts settle only their captured running jobs; the parent/chain handoff is rechecked under the recovery owner predicate, while a thread followup is deliberately afterward and is not protected from a later replacement owner.

## Observed skill use (THR-203 step 1A)

`skill_use_events` is an additive, observation-only ledger, separate from
`custom_skill_materializations` (delivery) and `custom_skill_events` (lifecycle).
A row attributes the signal to `org_slug`, permanent `agent_name`, nullable
`task_id`, `session_context` (task/thread/wake/dream), runtime `session_id`,
`executor`, provider `provider_session_id`, `skill_slug`, `skill_source`
(custom/bundled/unknown), nullable `custom_skill_id`, `signal_kind`, `observed_at`
and stable `source_ref`. A unique index on org/runtime session/executor/source
reference/slug makes repeated observation idempotent. Existing lifecycle CHECKs
and delivery behavior are unchanged. Opening an existing org DB installs the
missing table and then its index idempotently; no table rebuild or backfill.

| Executor | Signal and seam | Coverage limits |
| --- | --- | --- |
| Claude | `invoked`: assistant `tool_use` named `Skill`, read after return from `~/.claude/projects/<cwd-slug>/<provider-session-id>.jsonl` | Exact submitted user prompt must occur once; only subsequent assistant entries before the next user text count. Provider identity must match. This distinguishes resumed provider history from the runtime invocation. |
| Codex | `opened_skill_file`: successful completed `command_execution` in existing `exec --json` stdout | Only simple `cat`, `sed -n N[,N]p`, or `head -n N` reads using absolute paths under the workspace's `.agents/skills/<slug>/SKILL.md` or `.claude/skills/<slug>/SKILL.md`, optionally wrapped in `sh/bash/zsh -c/-lc`. Relative paths are unobserved because the command's cwd is not verified; the launch workspace is insufficient evidence. Complex shell, other read tools, external roots and rollout-only reads are gaps. This records an open, not invocation/compliance. |
| OpenCode | No signal recorded | Existing JSON output is used for usage/session identity; no verified skill-specific observation seam is implemented. |
| Pi | No signal recorded | No verified skill-specific observation seam is implemented. |

Only built-in Claude/Codex normal returns with a parsed provider ID supply
capture input. Timeouts/nonzero returns, interrupted paths without a result,
custom adapters, and provider format/path changes may be unobserved. Claude
uses the conventional cwd encoding (non-ASCII-alphanumeric characters become
`-`); it never searches alternative files. Prompt normalization/compaction or
repeated identical prompts may prevent attribution and discard the observation.
Custom IDs are resolved by org/slug at observation time (including historical
retired rows); release package paths identify bundled slugs, otherwise unknown.
These rows establish neither delivered version nor eligibility.

The optional observer runs in at most two daemon threads, with no join on the
session path. Busy workers/DB, shutdown and missing/unreadable inputs lose the
observation. Transcript parents and leaf are opened no-follow; only a stable,
singly linked regular file at the expected session path is accepted. Each parse
has 4 MiB total, 512 KiB per line, 20,000 lines, 256 signals and a cooperative
100 ms deadline; a failure discards the entire batch and logs only the exception
class. The line budget leaves headroom above the measured production maximum
of 318,269 bytes (20 transcripts, 436,610–2,772,852 bytes and 43–425 lines).
A line over 512 KiB still discards all signals, including a valid prefix: skipping
it could hide a user boundary or provider mismatch and misattribute later use.
This deliberately undercounts oversized sessions. These are cooperative work
bounds, not hard OS I/O guarantees. Files are
read before acquiring a DB writer. A separate existing-DB connection uses zero
busy waiting and a separate cooperative 100 ms write budget with rollback on
failure. Observation never drives completion, retry, callbacks or accounting;
raw prompts, commands and transcripts are never persisted or logged by it.
No argv, hooks, permissions, settings, delivery or required CI changes are made.

No API/CLI read surface is added. For an operator's existing org DB:

```sql
SELECT org_slug, agent_name, task_id, session_context, session_id, executor,
       skill_slug, skill_source, custom_skill_id, signal_kind,
       provider_session_id, observed_at, source_ref
FROM skill_use_events ORDER BY id DESC LIMIT 100;
```
