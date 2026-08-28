# Web And CLI

## Web UI

Layer rules, boundary rules, and agent-callback omissions live in `web/ARCHITECTURE.md`. Full design: `docs/superpowers/specs/2026-05-14-web-ui-design.md`.

Every browser-callable daemon route maps to one TypeScript function in `web/src/lib/api/`. Two paired tests enforce this:

- Python: `tests/contract/test_openapi_snapshot.py` pins OpenAPI to `tests/contract/openapi.json`. Regenerate intentional changes with `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
- TypeScript: `web/src/test/openapi-coverage.test.ts` asserts every documented path is either included with a TS mirror or excluded with justification.

### Settings

The Settings surface ships as a full page (`web/src/features/settings/SettingsPage.tsx`) at the `/orgs/:slug/settings/*` route, entered from the footer-pinned **Settings** item in the Sidebar, with exactly three left sub-nav panels: Assistant · Organization · Executors. The Settings root, retired `system` and `agents` subroutes, and unknown subroutes resolve to Assistant with replace navigation. (The TopBar gear button and `SettingsDialog` are prototype/design-preview surfaces only — not production entry points.) It shows:

- **Assistant** — assistant status, setup/recovery, and assistant executor binding.
- **Org** (editable, Phase 2) — org-level settings: session timeout override, dreaming schedule (enabled, schedule time/timezone, catch-up-on-startup, agent mode, include/exclude agent names), threads config (enabled, default turn cap, invocation timeout), and **working_hours** (THR-035: the Work-Hours Config UI — feature on/off switch, org-level eligibility selector, and the raw per-tier schedule blocks `default` / `teams` / `overrides`).
- **Executors** — effective machine executor registry, custom CLI lifecycle, and recovery.

The response includes operator-only `reviewer_agents` and `threads.default_turn_cap`; neither has a browser control. Removing the former System rail does not move queue-worker or maximum-orchestration-step facts into Health.

**Backend routes:**

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/orgs/{slug}/settings` | Read-only System + Org snapshot (includes the raw per-tier `working_hours` blocks for the reconciliation view) |
| `PUT` | `/api/v1/orgs/{slug}/settings/org` | Partial-update editable Org settings |
| `PUT` | `/api/v1/orgs/{slug}/settings/teams` | Worker-membership editing for teams |
| `GET` | `/api/v1/orgs/{slug}/work-hours/next-wakes` | Preview the next N wake timestamps for an agent's resolved effective schedule |

The serializer is an allow-list: no secret fields (permission_mode, codex_sandbox_mode, feishu credentials, daemon bind/port, allow_rules) are ever serialized. `extra='forbid'` on the PUT body rejects unknown/sensitive keys with 422. `save_org_config` deep-merges only allow-listed keys (`dreaming`, `threads`, `session_timeout_seconds`, `working_hours`) and carries through all unmanaged blocks verbatim. Tests recursively assert key-safety invariants (`tests/daemon/test_routes_settings.py`).

**Work-Hours Config (THR-035):** `working_hours` writes reuse the existing validate-then-atomic-write path in `save_org_config` — the candidate config is validated by `_build_org_config` / `_parse_working_hours` (the same path config-load uses), so an invalid config can never reach disk; the last-known-good keeps running. `enabled` is a single feature-level switch (never a per-tier or per-agent leaf); eligibility (`agents`) is a single org-level gate. A pre-flight validates working_hours agent/team names against the live roster (422 on unknown) before any write. Every working_hours write emits an audit row scoped to `config:working_hours` (who/when/before→after/tiers) via `AuditLogger.log_org_config_write` — reusing the established generic-scope-id convention on `audit_log.task_id` (no column change, no real-TASK-id overload). Validation is server-authoritative; the client does cheap format hints only. Routine-task editing is **read-only in MVP** (Phase 2 is the agent-contract-file write surface).

**Hot-reload:** Changes apply on next consumer read — dreaming scheduler picks up changes within ~1 min; threads/compose read on next request; session timeout applies to next session spawn. No daemon restart required.

The shipped frontend surface is `web/src/features/settings/SettingsPage.tsx` (left sub-nav + field panel per sub-route) with `lib/api/settings.ts`, `hooks/settings.ts`, and a `settings` domain in `DataContext`; the older `SettingsDialog.tsx` remains in use only inside prototype/design-preview surfaces.

### Agents page

The Agents page (`web/src/features/agents/`) shows the active agent roster plus pending enrollments. Each agent detail drawer now includes (Phase 2):

- **Repositories** — `repos` map from org/agents/<name>.md frontmatter (THR-095), shown as badge chips in the detail header.
- **System prompt** — read-only, collapsible. Sourced from the `system_prompt` field on the existing `GET /agents` response (additive Phase 2 field).
- **Model** — per-agent model string (`model` field in `GET /agents`, additive THR-067 field). Web UI field is PR-2 (separate follow-up).

Teams membership editing (add/remove workers only — manager reassignment is founder-gated) is available via `PUT /settings/teams`, wrapping `TeamsRegistry` mutators with `validate_team_membership` consistency checks and 409 rollback on drift.

**Backend:** The `GET /agents` response now includes `repos`, `system_prompt`, and `model` fields (additive, `allow_rules` remains excluded). The `PUT /agents/{agent}/model` route sets or clears the per-agent model (see below).

Build and dev commands:

```bash
scripts/build_web.sh
cd web && npm run dev
happyranch web
```

The SPA fetches the daemon bearer token once via `GET /api/v1/auth/bootstrap`, which is localhost-gated, then caches it in `sessionStorage` and attaches it to HTTP and SSE calls. CLI bearer-token behavior is unchanged.

## CLI

The CLI is an HTTP client. Start the daemon first.

```bash
scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force     # graceful shutdown (default daemon needs --force)
happyranch web [--no-open]
```

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` > auto-infer only when exactly one org exists > error. Container-level commands take no `--org`.

System assistant commands are container-level:

```bash
happyranch assistant init [--repair|--reconfigure]
happyranch assistant status
happyranch assistant
```

`happyranch assistant` shows the system assistant configuration status;
`happyranch assistant init` and `happyranch assistant register` manage the
assistant. It does not take `--org`.

### Task work-status summary (TASK-5522)

`happyranch details <task_id>` prints a compact **Work status** block derived
server-side from the task record + audit rows (`runtime/daemon/work_status.py`)
and served on the existing task-detail envelope — no extra endpoint:

```
Work status: Stale-but-alive — no substantive update recorded
  Start:      2026-08-23 21:06:02
  Heartbeat:  2026-08-23 21:39:30 (fresh)
  Update:     No substantive update recorded
```

- The **Start** line is the current-session `session_start` audit timestamp
  (latest assigned-agent session; a prior session's receipts never count).
- The **Heartbeat** line is the last heartbeat with an explicit freshness
  suffix using the existing 60-second semantics (`fresh`/`stale`/
  `unavailable`) — it never claims execution progress.
- The **Update** line comes ONLY from a real `progress` audit receipt
  (timestamp + concise agent-written milestone). When none is in scope it
  prints the explicit `No substantive update recorded`; a live session whose
  start or last receipt is ≥ 5 minutes old renders `Stale-but-alive …`
  (policy `STALE_PROGRESS_AFTER_SECONDS = 300`).
- Terminal / pending / escalated / parked-on-block tasks print `Not
  applicable` with a reason — never an implied live agent.

The full audit log (including inline `progress` messages) is unchanged.

The founder-facing web surface is the **A-mode Cmd-K dock** (structured chat
docked in the AppShell, toggled via the TopBar / Cmd-K shortcut). Assistant
configuration (status / init / register / repair) is served over four HTTP routes
(in `INCLUDED_PATHS` with TS mirrors in `web/src/lib/api/assistant.ts`).
There is no standalone `/assistant` web page, no xterm terminal, and no
"Open full session" escape hatch — the dock is the sole assistant surface.

### Org portability (Slice A)

CLI-only, relocation-only safety surfaces (no UI / TS client / browser
contract). Slice A is preflight + reconciliation only — it creates no archive
and performs no export/import.

```bash
# Read-only: classify every direct org-root child + report quiescence blockers
happyranch orgs portability-preflight <slug>

# Founder/master-bearer-only: reconcile exactly one confirmed zombie
happyranch orgs reconcile-portability <slug> --from-file /tmp/reconcile.json
```

`reconcile-portability` request JSON names one candidate plus evidence and a
disposition (`cancel` or `consume_result`):

```json
{"candidate_task_id": "TASK-123", "disposition": "cancel", "evidence": {"reason": "dead pid + stale heartbeat"}}
```

The `--from-file` path must be absolute. See `docs/agent-guides/features-and-invariants.md`
(Org Portability) and `protocol/05c-orchestrator.md` (Organization portability)
for the exhaustive root allow-list (including `work_hours`), quiescence/zombie
reporting, the conservative schedule policy (any armed or firing schedule
refuses, with existing-control remedies only), and reconciliation limits.

Full founder-facing CLI docs: `skills/happyranch/SKILL.md`.

### PR CI wait / guarded merge entrypoints

Two CLI entrypoints (invoked as jobs or on task resume, not as `happyranch` subcommands) provide the PR CI polling and guarded-merge mechanisms:

```bash
# Poll job (submitted via happyranch jobs submit):
python -m runtime.daemon.pr_ci_waiter \
  --repo owner/repo --pr N --head-sha <40-char-sha> \
  --expected-check "Python CI" --expected-check "Web CI" \
  --timeout-seconds 3600 --settle-seconds 120 --poll-interval-seconds 15

# Merge (triggered by resumed task):
python -m runtime.daemon.pr_ci_merge \
  --org <org-slug> --repo owner/repo --pr N --head-sha <40-char-sha> \
  --merge-method squash --ci-verdict ci_pass \
  --review-task-id TASK-xxx --qa-task-id TASK-yyy
```

Both print structured JSON verdicts to stdout and exit with mapped codes (0 = success).
The review/QA evidence extraction follows the **Merge-evidence contract** in
`protocol/00-completion-contract.md`: the canonical vocabulary
`APPROVE | REQUEST_CHANGES | BLOCK | PASS | REVISE | FAIL`, NON-NULL structured
`verdict` primary (canonical token only), serialized `null` (the durable
recall producer's representation of legacy/no-structured rows) using the
strict annotated prose (`Verdict: PASS — rationale`) fallback, and fail-closed
rejection of missing/contradictory/malformed/ambiguous evidence and unusable
non-null structured values.
The poll job runs with `review_required=false` through the existing jobs path; agents never
get raw `gh pr merge` grants. The full workflow narrative (submit → blocked → resume → inspect →
merge/revise) is documented in `protocol/skills/jobs/SKILL.md` and
`docs/agent-guides/features-and-invariants.md`.

### Per-agent model selection

Set or clear the model an agent uses via the `set-model` CLI command (mirrors `set-executor`):

```bash
# Set a model
happyranch set-model --org <org> dev_agent --model claude-sonnet-5

# Clear — revert to CLI default:
happyranch set-model --org <org> dev_agent
```

The backend route is `PUT /api/v1/orgs/{slug}/agents/{agent_name}/model` with payload
`{"model": "<id>" | null}`. It reconciles the org `.md` frontmatter (`model:` field) and
the org/agents/<name>.md frontmatter in one call (THR-095).

When a model is set AND the executor profile has a `model_arg` template (all four built-in
profiles do — verified per CLI), the executor injects the substituted model flags into the
CLI argv at launch time. When unset, each CLI uses its own default model (today's behavior
for every existing agent).

### Executor binary registration

Register the absolute path to each executor CLI binary so the daemon can locate it at
spawn time (THR-085). The daemon resolves binaries exclusively from the machine-local
``executors.json`` registry at launch — there is no PATH fallback (THR-107 seq155).
Registration is the sole availability gate; headless daemons and fresh machines must
have every executor explicitly registered.

**Exception — custom-adapter profiles:** profiles with
``command_adapter_id: custom-adapter:<id>`` use the exact founder-APPROVED,
hash-verified absolute adapter executable as their launch artifact — they do **not**
require a separate ``executors.json`` record keyed by their profile name.
All other profiles (built-ins, generic-cli custom) always resolve through
``executors.json``.

```bash
# Register with explicit path (required):
happyranch executor-binaries register claude --path /opt/homebrew/bin/claude

# List all registered binaries:
happyranch executor-binaries list

# Conditionally remove a binary registration (kind + exact path must match):
happyranch executor-binaries remove <kind> --expected-path <absolute-path>
```

`--path` is **required** — omission does NOT fall back to PATH resolution
(THR-107 seq155). The operator must supply an explicit absolute path.

``happyranch executor-binaries remove`` atomically deletes a binary registration
when both ``kind`` and ``--expected-path`` match the stored record exactly:
200 prints the removed registration; 404 reports no registration; 409 reports a
stale observed path (refresh with ``list`` and retry); 422 reports validation/
built-in-protection/name-mismatch errors. This command removes machine-local
binary registrations only — it does **not** delete adapters, profiles, or other
daemon state.

**Custom-adapter lifecycle management (THR-107 slices 1–3).** The ordinary
Settings/onboarding "Connect a CLI → connect a custom CLI instead" flow is
now instant: the founder mints a token by naming the CLI — nothing else —
the candidate CLI's copy-pasted script both writes its wrapper at the
daemon-issued path and POSTs it directly, and the browser auto-finishes the
connection the moment it lands — no PENDING wait, no founder-approval
click, no separate Bind step. The wrapper's own `/connect` POST declares
its `workspace_adapter_id` (which workspace-bootstrap convention its
agents should use); the founder never picks this — only the wrapper author
knows which convention their CLI expects. Approved-unbound recovery
affordances, the pending-approval queue, and the standalone Bind card were
**removed from the ordinary UI** in slice 3 (not hidden behind an advanced
panel) — a connect that doesn't finish shows a retryable "Connection
failed" card instead.

Adapter-backed custom CLIs are surfaced inside **Settings → Executors →
Custom CLIs**: their approved executable is joined to the profile row by the
``command_adapter_id: custom-adapter:<id>`` reference. The authenticated
``DELETE /api/v1/runtime/adapters/{adapter_id}`` route remains available for
API-level cleanup — removal only deletes the durable registration entry
(never the on-disk executable) and writes an ``adapter_removed`` audit row
(scope ``adapter:<id>``).

The legacy ``POST /runtime/adapters/{id}/approve|reject|bind-profile`` routes
(and their ``happyranch``-adjacent TS bindings in ``web/src/lib/api/
adapters.ts``) are preserved, unchanged, as **operator-only one-time
disposition tooling** — no normal-flow UI calls them anymore
(``tests/contract/route-classification.json`` reclassifies them as excluded,
no browser consumer). Reach for them only for manual/scripted recovery of a
legacy PENDING/approved-unbound record; a new custom CLI should always use
the ordinary Connect flow instead.

### Token usage

`happyranch tokens` shows `session_token_usage`. Default is the most recent rows;
a `--by-*` flag (mutually exclusive) switches to a rollup:

```bash
happyranch tokens --by-agent | --by-task | --by-thread | --by-purpose
```

`--by-purpose` groups by `invocation_purpose` (route `group_by=purpose`). Filters
(`--since`, `--thread-id`, `--agent`, `--purpose`, `--scope-type`,
`--scope-id`, `--task-id`) AND-compose with any view.

Rollup modifiers (presentation-side; require a `--by-*` flag):

- `--top N` — rank by churn (`total`) DESC and keep the top N; ties: sessions DESC then key ASC.
- `--over-threshold N` — keep only groups whose churn strictly exceeds N (applied **before** `--top`); empty result prints a "nothing would alert" line.

**Churn invariant:** `total = input + output + reasoning`. `CacheR`
(cache reads) rides in its own column and is **never** folded into `total`
or used to sort/threshold — it overstates burn ~10–100×.

The `--by-agent`/`--by-thread` rollups add a **Model** column
(none on `--by-task`/`--by-purpose`). Its label is classified at render time —
a single presentation constant `MODEL_FIX_CUTOVER_TS` draws the pre/post line,
never SQL:

| Label | Meaning |
| --- | --- |
| `<model-id>` | one observed model |
| `(mixed)` | >1 model, or observed+NULL mixed, or NULL spanning codex+claude |
| `(cli-unreported)` | all-NULL codex (codex emits no model field) |
| `(unknown — pre-fix)` | all-NULL claude, all before the cutover (frozen history) |
| `(unknown — ANOMALY)` | all-NULL claude, any at/after the cutover (parser-drift canary) |

The founder dashboard carries a read-only **Top token threads** card (a
window selector for 24h/7d/30d) backed by the same `/tokens?group_by=thread`
route. It ranks threads by churn (`total`) DESC client-side, shows cache reads
as a muted secondary number (never in the bar or the rank), and labels each
thread's Model with the same precedence as the CLI table above.

## Agent-Side Callbacks

These are invoked by skills inside agent sessions. Do not invoke them by hand; doing so falsifies audit data.

- `happyranch report-completion`
- `happyranch progress`
- `happyranch memory {add,update,promote,reindex}`
- `happyranch manage-agent`
- `happyranch manage-repo`
- `happyranch dispatch`
- `happyranch threads {reply,decline,dispatch}`

Callbacks should use `--from-file <path>` where payloads have multiple fields. **The path MUST be absolute** (e.g. `/tmp/completion.json`). A relative path silently resolves against the agent's cwd and can litter stray files under the runtime orgs root. The CLI rejects relative paths with a clear error in the callback family (`report-completion`, `threads reply/decline/dispatch/compose`). See `docs/agent-guides/agent-executors-and-permissions.md`.
