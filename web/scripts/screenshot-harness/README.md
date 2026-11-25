# FE screenshot harness

A **composable** module every frontend task imports to capture real-viewport
Playwright screenshot evidence (light + dark) for the fidelity loop. You
**compose** these primitives in a tiny task script — you do **not** fork a copy.

Canonical decision ruling: shared KB entry
`fe-screenshot-harness-mode-decision-tree` (`happyranch kb get fe-screenshot-harness-mode-decision-tree`).
This README is the local, runnable mirror.

## Guardrails (why it looks the way it does)

- **No new npm dependency.** The static / `/api` mock servers use
  only Node built-ins. Screenshots shell out to the venv Playwright via the
  `playwright-cli` binary — there is **no** imported npm `playwright`/`@playwright/test`.
- **Zero Web-CI footprint.** Everything here is plain Node ESM (`.mjs`) under
  `web/scripts/`. `eslint` only lints `src`, `tsc -b` only includes `src`, and
  `vite build` only bundles what `index.html`/`src` import — so these files never
  enter the lint / typecheck / build graph. Add freely; CI stays green.
- Output (`out/`) and vite scratch (`.screenshot-harness-tmp/`) are git-ignored.

## The three modes — pick the FIRST that fits

| # | Mode | Use when | Fidelity | Isolation | Setup | Needs `dist` build |
|---|------|----------|----------|-----------|-------|--------------------|
| C | prop-driven, no providers | one presentational component you can build from explicit props (no data hooks / QueryClient / Router) | low | highest | lowest | no (vite dev) |
| B | standalone dist-CSS | a pure CSS / token / `:focus-visible` shot of arbitrary markup — no React runtime | high (CSS) | high | low | yes (CSS only) |
| A | prod build + node `/api` mock | full built app + realistic REST data (full-page / route shots, multi-state) | highest | medium | medium | yes |

Rule of thumb: use the **cheapest / most-isolated** mode that still shows what
you must prove. Escalate rightward only when the cheaper mode cannot render the
state (real data → A). See the KB entry for the full
per-mode gotchas (MEM-007/010/014/016).

Modes A/B need a built app — run `npm run build` in `web/` first. That build
uses the **production** UI flag profile (THR-305), which hides unfinished
surfaces such as Work Hours; to capture them, build with
`npm run build:local` (development defaults plus your optional
`config/features.local.json`). See `web/ARCHITECTURE.md` § Build-time UI flags.

## Quick start (the worked example)

Enable failure cases use the same harness with test-side native IO and the real
ConnectionDialogBody/HostSection, providers, parser and read hook. Produce the
synthetic trace through the retained helper mount, native Main and relay
selections using `ENABLE_PREPARATION_TRACE=/absolute/task-output/production-trace.json`.
Then, after the documented locked Web dependency setup, run from `web/`:
`node scripts/screenshot-harness/enable-failure-cases.mjs /absolute/task-output/production-trace.json /absolute/task-output/screenshots`.
The adapter uses the existing installed Vite/Playwright primitives and an
OS-selected private port. It exercises literal reasons, held read ordering,
completed/unknown controls, locale preservation and close/reopen, then captures
inventory with Off, inventory unavailable and genuine unknown in EN/zh-CN at
390×844 and 1440×900 (12 images). This is synthetic bridge presentation, not
installed bus/polkit or live-host acceptance; execution results must be reported
separately from source delivery. No new dependency or CI trigger is added.

```bash
cd web
npm run build                                  # modes A/B need dist/
node scripts/screenshot-harness/demo.mjs all   # or: a | b | c
# -> web/scripts/screenshot-harness/out/mode-<x>-<light|dark>.png
```

`demo.mjs` is the reference: copy the block for your mode and adapt the content.

The stateful Coding setup cases also use the existing ordinary-build CDP runner:
`node scripts/w4a-browser-evidence.mjs --slice workflow-setup --dist dist
--workflow-fixture /absolute/fixture.json --out /absolute/evidence`. Produce the
synthetic fixture by setting `WORKFLOW_SETUP_BROWSER_FIXTURE=/absolute/fixture.json`
on the repository's `bash scripts/test-workflow-schema-load.sh
--setup-admission=first_save` command. `--workflow-case A00.2,W01,W02,W03,W04,W05`
selects cases; `--workflow-case W05-switch` isolates locale preservation. Locale
changes use a real background-tab storage event with zero visibility transitions;
a separate same-locale foreground control observes normal focus refresh. These
browser cases observe the shipping provider and request
ledger; the private schema/ASGI fixture separately owns persistence and auth.
They add no required CI execution or trigger.

## Composing each mode in your own task script

```js
import {
  modeAProdApi, modeBDistCss, modeCProp,
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

```

### Primitives (hand-compose an unusual case)

- `createServer({ root, api })` → `{ url, port, server, close }` — static
  files (SPA fallback to `index.html`) + `/api/*` JSON mock (unmatched → `{}`).
- `defaultApiRoutes({ token, orgs })` — the routes the
  shell needs to boot (`/auth/bootstrap`, `/orgs`). Spread first, then append your overrides.
- `startViteHarness({ importPath, exportName, render })` → `{ url(theme), stop }`
  — the installed Vite entry runs under the current Node executable and serves
  a generated no-provider mount (mode C). `stop()` sends TERM, escalates to KILL
  after five seconds if needed, and waits for child closure before removing the
  generated files. Startup and teardown failures are bounded and fail the run.
- `capture({ url, out, viewport, theme, appTheme, prep, settleMs })` — drives
  `playwright-cli` (open → resize → goto → theme → prep → shot → close). Set
  `appTheme:false` when the theme is baked into the URL (standalone modes).

## Notes

- Viewport defaults to **1440×900** for app modes — the shell is desktop-only
  (MEM-004). Standalone modes default to a small strip.
- Save routine/automatic screenshots, logs and QA/review evidence in
  `output/<task_id>/`, report `output_dir`, and retrieve via `happyranch recall`.
  For binary handoff, upload a fresh task attachment with
  `happyranch attach-upload --org happyranch --as-agent <your_agent_id> --file /absolute/source`
  and use decision attachments. Shared Artifacts are reserved for persistent
  downloadable deliverables under the [Shared Artifacts contract](../../../docs/agent-guides/contracts/shared-artifacts.md).
- This is dev/test **tooling**, not shipped app code. It touches nothing under
  `protocol/`, the permission model, auth, or the DB schema.
