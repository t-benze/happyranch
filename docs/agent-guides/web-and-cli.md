# Web And CLI

## Web UI

Layer rules, boundary rules, and agent-callback omissions live in `web/ARCHITECTURE.md`. Full design: `docs/superpowers/specs/2026-05-14-web-ui-design.md`.

Every browser-callable daemon route maps to one TypeScript function in `web/src/lib/api/`. Two paired tests enforce this:

- Python: `tests/contract/test_openapi_snapshot.py` pins OpenAPI to `tests/contract/openapi.json`. Regenerate intentional changes with `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
- TypeScript: `web/src/test/openapi-coverage.test.ts` asserts every documented path is either included with a TS mirror or excluded with justification.

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
scripts/daemon.sh stop
happyranch web [--no-open]
```

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` > auto-infer only when exactly one org exists > error. Container-level commands take no `--org`.

System assistant commands are container-level:

```bash
happyranch assistant init [--repair|--reconfigure]
happyranch assistant status
happyranch assistant
```

`happyranch assistant` attaches to the daemon-owned PTY for the runtime-global
system assistant. It does not take `--org`.

The same surface is founder-facing in the web app at `/orgs/:slug/assistant`
(`web/src/features/system-assistant/`): status / init / register / repair over the
four HTTP routes (now in `INCLUDED_PATHS` with TS mirrors in
`web/src/lib/api/assistant.ts`), plus an in-browser xterm.js terminal attached to the
`/assistant/session` WebSocket PTY. Because browsers cannot set the `Authorization`
header on `new WebSocket()`, the browser authenticates by offering the bearer token as
the `Sec-WebSocket-Protocol` subprotocol `happyranch.bearer.<token>` (THR-006 Option A);
the daemon validates and echoes it back. The WS route is not in OpenAPI (FastAPI omits
WS), so it is not part of the `openapi-coverage` route set. See
`docs/superpowers/specs/2026-06-12-system-assistant-web-ui-design.md`.

Full founder-facing CLI docs: `skills/happyranch/SKILL.md`.

### Token usage

`happyranch tokens` shows `session_token_usage`. Default is the most recent rows;
a `--by-*` flag (mutually exclusive) switches to a rollup:

```bash
happyranch tokens --by-agent | --by-task | --by-thread | --by-talk | --by-purpose
```

`--by-purpose` groups by `invocation_purpose` (route `group_by=purpose`). Filters
(`--since`, `--thread-id`, `--talk-id`, `--agent`, `--purpose`, `--scope-type`,
`--scope-id`, `--task-id`) AND-compose with any view.

Rollup modifiers (presentation-side; require a `--by-*` flag):

- `--top N` — rank by churn (`total`) DESC and keep the top N; ties: sessions DESC then key ASC.
- `--over-threshold N` — keep only groups whose churn strictly exceeds N (applied **before** `--top`); empty result prints a "nothing would alert" line.

**Churn invariant:** `total = input + output + reasoning`. `CacheR`
(cache reads) rides in its own column and is **never** folded into `total`
or used to sort/threshold — it overstates burn ~10–100×.

The `--by-agent`/`--by-thread`/`--by-talk` rollups add a **Model** column
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

## Agent-Side Callbacks

These are invoked by skills inside agent sessions. Do not invoke them by hand; doing so falsifies audit data.

- `happyranch report-completion`
- `happyranch progress`
- `happyranch learning {add,update,promote,reindex}`
- `happyranch manage-agent`
- `happyranch manage-repo`
- `happyranch dispatch`
- `happyranch threads {reply,decline,dispatch}`

Callbacks should use `--from-file <path>` where payloads have multiple fields. See `docs/agent-guides/agent-executors-and-permissions.md`.
