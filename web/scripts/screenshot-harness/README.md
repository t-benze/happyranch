# FE screenshot harness

A **composable** module every frontend task imports to capture real-viewport
Playwright screenshot evidence (light + dark) for the fidelity loop. You
**compose** these primitives in a tiny task script — you do **not** fork a copy.

Canonical decision ruling: shared KB entry
`fe-screenshot-harness-mode-decision-tree` (`happyranch kb get fe-screenshot-harness-mode-decision-tree`).
This README is the local, runnable mirror.

## Guardrails (why it looks the way it does)

- **No new npm dependency.** The static / `/api` / WebSocket mock servers use
  only Node built-ins. Screenshots shell out to the venv Playwright via the
  `playwright-cli` binary — there is **no** imported npm `playwright`/`@playwright/test`.
- **Zero Web-CI footprint.** Everything here is plain Node ESM (`.mjs`) under
  `web/scripts/`. `eslint` only lints `src`, `tsc -b` only includes `src`, and
  `vite build` only bundles what `index.html`/`src` import — so these files never
  enter the lint / typecheck / build graph. Add freely; CI stays green.
- Output (`out/`) and vite scratch (`.screenshot-harness-tmp/`) are git-ignored.

## The four modes — pick the FIRST that fits

| # | Mode | Use when | Fidelity | Isolation | Setup | Needs `dist` build |
|---|------|----------|----------|-----------|-------|--------------------|
| C | prop-driven, no providers | one presentational component you can build from explicit props (no data hooks / QueryClient / Router) | low | highest | lowest | no (vite dev) |
| B | standalone dist-CSS | a pure CSS / token / `:focus-visible` shot of arbitrary markup — no React runtime | high (CSS) | high | low | yes (CSS only) |
| A | prod build + node `/api` mock | full built app + realistic REST data (full-page / route shots, multi-state) | highest | medium | medium | yes |
| D | prod + `/api` + WS mock | the A-mode System Assistant **dock**, whose transcript rides the WebSocket | highest (dock) | low | highest | yes |

Rule of thumb: use the **cheapest / most-isolated** mode that still shows what
you must prove. Escalate rightward only when the cheaper mode cannot render the
state (real data → A; the WS transcript → D). See the KB entry for the full
per-mode gotchas (MEM-007/010/014/016).

Modes A/B/D need a built app — run `npm run build` in `web/` first.

## Quick start (the worked example)

```bash
cd web
npm run build                                  # modes A/B/D need dist/
node scripts/screenshot-harness/demo.mjs all   # or: a | b | c | d
# -> web/scripts/screenshot-harness/out/mode-<x>-<light|dark>.png
```

`demo.mjs` is the reference: copy the block for your mode and adapt the content.

## Composing each mode in your own task script

```js
import {
  modeAProdApi, modeBDistCss, modeCProp, modeDWsDock,
  // lower-level primitives if you need to hand-compose:
  createServer, defaultApiRoutes, startViteHarness, capture, findDist, findDistCss,
} from './scripts/screenshot-harness/harness.mjs';

const outDir = '/tmp/shots';

// A — full app + /api mock. Both themes captured.
await modeAProdApi({
  route: '/orgs/demo/threads',
  outDir, name: 'threads',
  api: [{ path: '/api/v1/orgs/demo/threads', json: { threads: [] } }], // page-specific
});

// B — arbitrary markup against the REAL compiled Tailwind (tokens/focus).
// Every class you use must exist somewhere in the app build (JIT).
await modeBDistCss({
  markup: `<button class="bg-primary text-primary-foreground h-9 rounded-md px-3">Send</button>`,
  outDir, name: 'button-focus',
});

// C — one component, explicit props, NO providers (vite dev).
await modeCProp({
  importPath: '@/design-system/primitives/Button', exportName: 'Button',
  render: `<Button variant='default'>Send</Button>`,
  outDir, name: 'button',
});

// D — the A-mode dock via a Node-builtin WS mock (no `ws` dep).
await modeDWsDock({
  route: '/orgs/demo/agents', outDir, name: 'dock',
  activeConv: 'c1',
  conversations: [{ id: 'c1', title: 'Weekly spend review', created_at: null, active: true }],
  historyByConv: { c1: [{ prompt: 'How much did we spend?', frames: [
    { type: 'turn_start' }, { type: 'text_delta', text: 'About $432.' }, { type: 'turn_end' },
  ] }] },
});
```

### Primitives (hand-compose an unusual case)

- `createServer({ root, api, ws })` → `{ url, port, server, close }` — static
  files (SPA fallback to `index.html`) + `/api/*` JSON mock (unmatched → `{}`) +
  optional WS mock.
- `defaultApiRoutes({ token, orgs, assistantConfigured })` — the routes the
  shell needs to boot (`/auth/bootstrap`, `/orgs`, and optionally the assistant
  status routes). Spread first, then append your overrides.
- `startViteHarness({ importPath, exportName, render })` → `{ url(theme), stop }`
  — vite dev serving a generated no-provider mount (mode C).
- `capture({ url, out, viewport, theme, appTheme, prep, settleMs })` — drives
  `playwright-cli` (open → resize → goto → theme → prep → shot → close). Set
  `appTheme:false` when the theme is baked into the URL (standalone modes).

`ws-mock.mjs` is the ~180-line dependency-free WebSocket server (RFC 6455
handshake + server→client framing) that powers mode D. It exists solely because
the repo has no `ws` package and the founder guardrail forbids adding one.

## Notes

- Viewport defaults to **1440×900** for app modes — the shell is desktop-only
  (MEM-004). Standalone modes default to a small strip.
- `happyranch artifacts put` defaults to the wrong org in the FE workspace —
  always pass `--org happyranch` when publishing evidence (MEM-010).
- This is dev/test **tooling**, not shipped app code. It touches nothing under
  `protocol/`, the permission model, auth, or the DB schema.
