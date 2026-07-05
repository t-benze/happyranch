# Web And CLI

## Web UI

Layer rules, boundary rules, and agent-callback omissions live in `web/ARCHITECTURE.md`. Full design: `docs/superpowers/specs/2026-05-14-web-ui-design.md`.

Every browser-callable daemon route maps to one TypeScript function in `web/src/lib/api/`. Two paired tests enforce this:

- Python: `tests/contract/test_openapi_snapshot.py` pins OpenAPI to `tests/contract/openapi.json`. Regenerate intentional changes with `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
- TypeScript: `web/src/test/openapi-coverage.test.ts` asserts every documented path is either included with a TS mirror or excluded with justification.

### Settings dialog

The Settings dialog opens from the TopBar gear button. It shows:

- **System** (read-only) — daemon-wide settings (CLI paths, session timeout default, orchestration limits) with restart-required badges.
- **Org** (editable, Phase 2) — org-level settings: session timeout override, dreaming schedule (enabled, schedule time/timezone, catch-up-on-startup, agent mode, include/exclude agent names), threads config (enabled, default turn cap, invocation timeout), and **working_hours** (THR-035: the Work-Hours Config UI — feature on/off switch, org-level eligibility selector, and the raw per-tier schedule blocks `default` / `teams` / `overrides`).

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

The frontend surface lives in `web/src/features/settings/SettingsDialog.tsx` with `lib/api/settings.ts`, `hooks/settings.ts`, and a `settings` domain in `DataContext`.

### Agents page

The Agents page (`web/src/features/agents/`) shows the active agent roster plus pending enrollments. Each agent detail drawer now includes (Phase 2):

- **Repositories** — `repos` map from agent.yaml, shown as badge chips in the detail header.
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

The founder-facing web surface is the **A-mode Cmd-K dock** (structured chat
docked in the AppShell, toggled via the TopBar / Cmd-K shortcut). Assistant
configuration (status / init / register / repair) is served over four HTTP routes
(in `INCLUDED_PATHS` with TS mirrors in `web/src/lib/api/assistant.ts`).
There is no standalone `/assistant` web page, no xterm terminal, and no
"Open full session" escape hatch — the dock is the sole assistant surface.

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
the workspace `agent.yaml` (`model:` key) in one call.

When a model is set AND the executor profile has a `model_arg` template (all four built-in
profiles do — verified per CLI), the executor injects the substituted model flags into the
CLI argv at launch time. When unset, each CLI uses its own default model (today's behavior
for every existing agent).

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
