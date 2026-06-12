# System Assistant — Web UI Design (Phase 2 build)

- **Task:** TASK-188 (design) → TASK-192 (build)
- **Status:** APPROVED — founder-signed-off Phase-2 build (THR-006). Rulings:
  G1 browser→WS auth = **Option A** (`Sec-WebSocket-Protocol` subprotocol);
  G2 = `@xterm/xterm` + `@xterm/addon-fit` approved; scope = all 4 capabilities
  (§2.1–2.4); phasing = ship 2a+2b together; this spec lands in the build PR.
- **Author:** engineering_manager (design) · dev_agent (build)
- **Base:** happyranch @ `main` (c87d797)
- **Depends on (merged):** `2026-06-08-system-assistant-design.md`, `2026-10-assistant-self-registration-design.md`
- **Scope:** Surface the already-merged System Assistant backend in the existing React web app.

## 0. One-paragraph summary

The System Assistant backend (status / init / register / repair HTTP routes + a
WebSocket PTY at `/assistant/session`, all behind `require_token()`) is merged and
CLI-exercised. This spec adds a founder-facing **System Assistant** page to the web
app that (1) shows status, (2) drives init / repair / reconfigure, (3) registers /
switches the executor, and (4) attaches an in-browser terminal (xterm.js) to the PTY.
Items 1–3 are buildable within the engineering_manager authority boundary. Item 4
required a founder ruling because the browser cannot send the bearer token to the
existing WS route without a daemon-side auth change — the founder ruled **Option A**
(the `Sec-WebSocket-Protocol` subprotocol), the one approved gated daemon edit.

## 1. Reuse vs. net-new (discovery result)

### Reuse (no change needed)
| Surface | What we reuse | Evidence |
|---|---|---|
| Frontend stack | Vite 5 + React 18 + TS + react-router-dom 6 + TanStack Query 5 + Tailwind 4 + Radix | `web/package.json`, `web/src/routes.tsx` |
| HTTP auth | `getToken()` bootstraps `GET /auth/bootstrap` → sessionStorage `happyranch.token`; `doFetch()` sets `Authorization: Bearer` | `web/src/lib/auth.ts`, `web/src/lib/api/client.ts:72` |
| Data layer | provider-aware `DataContext` (real vs mock), hooks under `web/src/hooks/`, api modules under `web/src/lib/api/` | `web/src/design-system/providers/AppProvider.tsx`, `web/src/hooks/*` |
| Page pattern | feature folder `web/src/features/<domain>/`, query-driven page, two-pane layout | `web/src/features/tasks/TasksPage.tsx`, `.../dashboard/DashboardPage.tsx` |
| Tests | Vitest + MSW + `renderWithProviders()` | `web/src/test/render.tsx`, `web/src/App.test.tsx` |
| All 4 HTTP assistant routes | already merged, header-auth works from browser today | `runtime/daemon/routes/assistant.py` |

### Net-new (this PR)
1. `web/src/lib/api/assistant.ts` — TS mirrors for status/init/register/repair (4 functions) + the PTY WS-url / bearer-subprotocol helpers.
2. `web/src/hooks/assistant.ts` — provider-aware query/mutation hooks + the PTY opener.
3. `web/src/design-system/providers/{_real,_mock}-assistant.ts` + `AssistantApi` on `DataContext`, wired into both providers.
4. `web/src/features/system-assistant/SystemAssistantPage.tsx` + `AssistantTerminal.tsx`.
5. Route entry in `web/src/routes.tsx` under `OrgLayout`; nav entry in the TopBar.
6. **Web Contract update:** move the 4 routes from `EXCLUDED_PATHS` → `INCLUDED_PATHS`
   in `web/src/test/openapi-coverage.test.ts`. No `openapi.json` regen — routes already
   in the snapshot; the WS route is not in OpenAPI at all (FastAPI omits WS).
7. Doc parity: `docs/agent-guides/web-and-cli.md`; this spec.
8. xterm.js terminal component + WS client (Option A subprotocol auth).
9. The one gated daemon edit (§3 G1): `_websocket_token_is_valid` + the `accept()` echo.

## 2. Capabilities

### 2.1 Status view
`GET /api/v1/assistant/status` → `{state, selected_executor, workspace_path, detail}`
where `state ∈ {uninitialized, configured, stale_or_broken}`. Render state badge,
selected executor, workspace path, and `detail` (shown when `stale_or_broken`). Poll
via React Query (`refetchInterval` 5s) — consistent with existing query usage, no
SSE/WS needed.

### 2.2 init / repair / reconfigure
- **Init**: `POST /assistant/init {reconfigure:false}` when `uninitialized` → prepares
  the registration workspace, then surface the self-registration instructions
  (the human launches their CLI in the workspace; the agent calls back `assistant register`).
- **Reconfigure**: `POST /assistant/init {reconfigure:true}` (confirm dialog — it closes
  sessions and clears config).
- **Repair**: `POST /assistant/repair` (no body) when `stale_or_broken`.
All three are plain authenticated POSTs via `doFetch` — no gated surface.

### 2.3 Register / switch executor
`POST /assistant/register {executor, command, argv}`. UI: pick executor
(claude / codex / opencode / pi / other-freeform) + command + argv; on submit, surface
structural errors verbatim: `assistant_registration_invalid`,
`assistant_executable_not_found`. Re-register preserves the workspace (server derives
`workspace_path` from runtime root, not user input). Single active executor is enforced
server-side. The UI carries no workspace input and says so in copy.

### 2.4 In-browser terminal
xterm.js + fit addon attached to the `/assistant/session` WS. Protocol (mirrors the CLI
reference client `cli/commands/assistant.py`):
- **stdin:** `term.onData(d => ws.send(d))` (text frames).
- **stdout:** `ws.onmessage = e => term.write(e.data)` (text frames).
- **resize:** on `term.onResize`, send the exact CLI control string
  `"__HAPPYRANCH_ASSISTANT_RESIZE__ <rows> <cols>"`; send once on open, then on every
  resize. Server parses it at `routes/assistant.py::_parse_resize_control`.
- **lifecycle:** only attach when `state === configured`; close code 1000 on unmount.
- **auth:** the browser bearer subprotocol from §3 G1.

## 3. Gated / sensitive surfaces — founder rulings (THR-006)

### G1 — Browser → WebSocket bearer-token auth  **(RULED: Option A)**
Browsers cannot set custom headers on `new WebSocket()`, so the token is offered via the
`Sec-WebSocket-Protocol` subprotocol: the browser does
`new WebSocket(url, ["happyranch.bearer." + token])`; the daemon reads the offered
subprotocol in `_websocket_token_is_valid`, validates it with `secrets.compare_digest`
(unchanged), and echoes the accepted subprotocol back on `websocket.accept(subprotocol=…)`.
The existing `Authorization: Bearer` header path is unchanged; `require_token()` and all
HTTP auth flows are untouched; the fail-closed reject-before-accept (`WS_1008_POLICY_VIOLATION`)
is preserved. This is the **only** daemon edit in the build.

### G2 — New top-level npm dependency  **(APPROVED)**
`@xterm/xterm` + `@xterm/addon-fit` (current scoped packages). Required only for §2.4.

### G3 — Web Contract change  **(within engineering_manager authority)**
Moving the 4 HTTP routes `EXCLUDED_PATHS → INCLUDED_PATHS` + adding TS mirrors. No new
daemon route, no `openapi.json` regen.

### G4 — New daemon route  **(none)**
No new daemon route. The only backend touch is the G1 WS-auth edit.

## 4. Build (ship 2a + 2b together)

The founder approved shipping §2.1–2.3 (status / init / register / reconfigure) and §2.4
(the terminal + G1 auth) in a single PR.

## 5. Build discipline

Surgical changes; component/unit tests alongside (Vitest + MSW); backend TDD for the
WS-auth edit; `gitnexus_impact` before editing any function/class/method with blast-radius
reported; `gitnexus_detect_changes` before push; Web Contract + `web-and-cli.md` + this
spec status updated in the **same** PR; test suites run **via happyranch jobs** (avoid the
1800s inline timeout); route through code_reviewer (APPROVE) → qa_engineer (PASS with
evidence); engineering_manager merges only on clean review + QA (no `--no-verify`,
no force-push). The daemon WS-auth edit respects the call-ordering / opaque-failure and
audit-row invariants and keeps `secrets.compare_digest` (THR-006 ruling 4).
