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
