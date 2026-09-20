/**
 * L6b — desktop capacity states from the BUILT app over loopback.
 *
 * Fail-closed venue. Two rules, both enforced and both reported:
 *
 *   1. `harness.mjs` defaults an unmatched `/api/` route to `{}` with status
 *      200 — a SILENT PASS that would make this evidence worthless. An
 *      explicit catch-all handler overrides it: any `/api/` path this script
 *      did not declare is RECORDED and marks the run FAILED.
 *   2. A Playwright context-level route aborts every request whose origin is
 *      not the task-owned loopback server. "Zero mock violations" measured at
 *      the server is server-local; this proves the browser could not reach
 *      anywhere else either.
 *
 * No request may fall through to a live daemon. Every response here is
 * synthetic injection and is NEVER evidence of backend persistence, audit,
 * restart or deployment.
 *
 *   node scripts/screenshot-harness/capacity-states.mjs --out <dir>
 */
import { mkdirSync, writeFileSync, readdirSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join, resolve } from 'node:path';
import { createServer, findDist } from './harness.mjs';

const PLAYWRIGHT = process.env.HR_PLAYWRIGHT_ROOT
  ?? '/tmp/task-6530-playwright/node_modules/playwright/index.js';
// Playwright ships CommonJS, so an ESM `import()` hands it back under
// `default`. Accept either shape rather than assuming one.
const playwrightModule = await import(PLAYWRIGHT);
const { chromium } = playwrightModule.default ?? playwrightModule;

const args = process.argv.slice(2);
const outDir = resolve(args[args.indexOf('--out') + 1] ?? './capacity-shots');
mkdirSync(outDir, { recursive: true });

const SLUG = 'alpha';
const REV_A = `sha256:${'a'.repeat(64)}`;
const REV_B = `sha256:${'b'.repeat(64)}`;

/** Synthetic example data. Not a real daemon observation. */
function snapshot(overrides = {}) {
  return {
    running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
    running_provenance: 'startup-resolved settings snapshot',
    persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 },
    next_start: { queue_workers: 3, host_global_session_cap: 10 },
    environment_shadowed: [],
    environment_warning: null,
    producer_envelope: 10,
    producer_components: {
      task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
    },
    effective_admission_cap: 10,
    effective_admission_reason: 'startup policy',
    warnings: [],
    revision: REV_A,
    restart_required: false,
    restart_pending: false,
    guidance: {
      queue_workers: 'Starting guidance 4-6.',
      host_global_session_cap: 'Starting guidance 11-13.',
      enforced: false,
    },
    authorization: 'daemon bearer required',
    ...overrides,
  };
}

const SETTINGS = {
  system: {
    claude_cli_path: { value: 'claude', restart_required: true },
    codex_cli_path: { value: 'codex', restart_required: true },
    opencode_cli_path: { value: 'opencode', restart_required: true },
    pi_cli_path: { value: 'pi', restart_required: true },
    session_timeout_seconds: { value: 1800, restart_required: true },
    queue_workers: { value: 3, restart_required: true },
    host_global_session_cap: { value: 13, restart_required: true },
    protocol_dir: { value: 'protocol', restart_required: true },
  },
  org: {
    session_timeout_seconds: null,
    reviewer_agents: [],
    dreaming: { enabled: false, schedule: { time: '02:00', timezone: 'UTC' }, catch_up_on_startup: false, agents: { mode: 'all', include: [], exclude: [] } },
    threads: { enabled: true, default_turn_cap: 500, invocation_timeout_seconds: null },
    working_hours: {
      enabled: false, agents: { mode: 'all', include: [], exclude: [] },
      default: { mode: 'always', window: null, interval: null, days: [], catch_up_on_startup: false },
      teams: {}, overrides: {},
    },
  },
};

/** The states captured. `capacity` is the GET body; `prep` drives the UI. */
const STATES = [
  { name: 'ordinary', capacity: () => snapshot() },
  {
    name: 'dirty-consequence',
    capacity: () => snapshot(),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '12');
      await page.fill('#capacity-reason', 'Queue delay grew after adding the second team.');
    },
  },
  {
    name: 'saved-restart-pending',
    capacity: () => snapshot({
      persisted_yaml: { queue_workers: 5, host_global_session_cap: 12 },
      next_start: { queue_workers: 5, host_global_session_cap: 12 },
      restart_pending: true,
      revision: REV_B,
    }),
  },
  {
    name: 'fallback-cap',
    capacity: () => snapshot({
      effective_admission_cap: 4,
      effective_admission_reason: 'Capability fallback binds because enforcement is unavailable.',
      warnings: ['Enforcement is unavailable on this platform; the conservative fallback cap binds.'],
    }),
  },
  {
    name: 'partial-override',
    capacity: () => snapshot({
      environment_shadowed: ['queue_workers'],
      environment_warning: 'The environment sets this value; a restart alone will not make the saved file win.',
      next_start: { queue_workers: 3, host_global_session_cap: 12 },
    }),
    prep: async (page) => {
      await page.fill('#capacity-workers', '5');
      await page.fill('#capacity-cap', '14');
    },
  },
  {
    name: 'runtime-unavailable',
    capacity: () => snapshot({
      effective_admission_cap: null,
      effective_admission_reason: 'No supervisor snapshot',
    }),
  },
  {
    name: 'not-set-in-file',
    capacity: () => snapshot({ persisted_yaml: { queue_workers: null, host_global_session_cap: null } }),
  },
  {
    name: 'representation-unavailable',
    capacity: () => snapshot({
      persisted_yaml: { queue_workers: 9007199254740992, host_global_session_cap: 10 },
    }),
  },
  {
    name: 'invalid-numeric',
    capacity: () => snapshot(),
    prep: async (page) => {
      await page.fill('#capacity-workers', '9007199254740993');
      await page.fill('#capacity-reason', 'attempting an unrepresentable value');
      await page.click('button[type="submit"]');
      await page.waitForSelector('#capacity-workers-error');
    },
  },
  {
    name: 'details-expanded',
    capacity: () => snapshot(),
    prep: async (page) => {
      await page.click('summary:has-text("Capacity details")');
    },
  },
];

const VIEWPORTS = [
  { name: '1440x1000', width: 1440, height: 1000 },
  { name: '1280x900', width: 1280, height: 900 },
];
const THEMES = ['light', 'dark'];

// ---------------------------------------------------------------------------

const undeclared = [];
const blockedExternal = [];
let currentCapacity = () => snapshot();

const api = [
  { path: '/api/v1/auth/bootstrap', json: { token: 'synthetic' } },
  { path: '/api/v1/orgs', json: { orgs: [{ slug: SLUG, root: '/synthetic' }] } },
  { path: `/api/v1/orgs/${SLUG}/settings`, json: () => SETTINGS },
  { path: `/api/v1/orgs/${SLUG}/settings/daemon-capacity`, json: () => currentCapacity() },
  { path: `/api/v1/orgs/${SLUG}/dashboard/summary`, json: { counts: {}, recent_tasks: [], escalations: [], agents: [] } },
  // REQUIRED OVERRIDE of harness.mjs's fail-OPEN unmatched-/api/ default.
  {
    path: /^\/api\//,
    handler: (req, res) => {
      undeclared.push(req.url);
      res.writeHead(599, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'undeclared api path — this venue is fail-closed' }));
    },
  },
];

const root = findDist();
const server = await createServer({ root, api });
const origin = new URL(server.url).origin;

// The inherited task TMPDIR is long enough to abort Chrome on its singleton
// socket, so the browser profile gets a short task-owned one.
const shortTmp = '/tmp/hr8553';
mkdirSync(shortTmp, { recursive: true });
process.env.TMPDIR = shortTmp;

// Use the INSTALLED Chrome rather than a downloaded browser: this host's
// playwright build and its browser cache are on different revisions, and
// nothing here may install a dependency.
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.HR_CHROME_PATH ?? '/usr/bin/google-chrome',
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});
const results = [];

try {
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      const context = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        colorScheme: theme,
        deviceScaleFactor: 1,
      });
      // Context-level fail-closed egress guard: nothing but the task-owned
      // loopback origin may be requested, by the app or by anything else.
      await context.route('**/*', (route) => {
        const url = route.request().url();
        if (url.startsWith(origin)) return route.continue();
        blockedExternal.push(url);
        return route.abort();
      });

      for (const state of STATES) {
        currentCapacity = state.capacity;
        const page = await context.newPage();
        await page.addInitScript((t) => {
          window.sessionStorage.setItem('happyranch.token', 'synthetic');
          window.localStorage.setItem('happyranch.theme', t);
        }, theme);
        await page.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'networkidle' });
        try {
          await page.waitForSelector('#capacity-panel-heading', { timeout: 15000 });
        } catch (error) {
          const body = (await page.textContent('body')) ?? '';
          console.error(`state ${state.name} never rendered the panel. body head: ${body.slice(0, 500)}`);
          throw error;
        }
        // Never settle on a loading frame.
        await page.waitForFunction(
          () => !document.body.textContent.includes('Loading daemon capacity'),
          { timeout: 15000 },
        );
        if (state.prep) await state.prep(page);
        await page.waitForTimeout(250);

        const file = `capacity-${state.name}-${viewport.name}-${theme}.png`;
        await page.screenshot({ path: join(outDir, file), fullPage: true });

        // Computed visibility + contrast receipts for the a11y cases, taken in
        // a REAL browser rather than from DOM presence.
        const receipt = await page.evaluate(() => {
          const visible = (node) => {
            if (!node) return null;
            const rect = node.getBoundingClientRect();
            const style = getComputedStyle(node);
            return rect.width > 0 && rect.height > 0
              && style.visibility !== 'hidden' && style.display !== 'none'
              && Number(style.opacity) > 0;
          };
          const alerts = [...document.querySelectorAll('[role="alert"]')];
          const detailsOpen = document.querySelector('details')?.open ?? null;
          const scrollW = document.documentElement.scrollWidth;
          const clientW = document.documentElement.clientWidth;
          const helper = document.querySelector('#capacity-workers-help');
          const helperStyle = helper ? getComputedStyle(helper) : null;
          return {
            alertCount: alerts.length,
            allAlertsVisible: alerts.every(visible),
            detailsOpen,
            horizontalOverflow: scrollW > clientW,
            scrollW,
            clientW,
            helperColor: helperStyle?.color ?? null,
            helperBackground: helperStyle ? getComputedStyle(document.body).backgroundColor : null,
            liveRegions: document.querySelectorAll('[aria-live]').length,
          };
        });
        results.push({ state: state.name, viewport: viewport.name, theme, file, ...receipt });
        await page.close();
      }
      await context.close();
    }
  }

  // Keyboard/focus pass in a real browser at the primary viewport.
  const kbContext = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' });
  await kbContext.route('**/*', (route) => (route.request().url().startsWith(origin)
    ? route.continue()
    : (blockedExternal.push(route.request().url()), route.abort())));
  currentCapacity = () => snapshot();
  const kbPage = await kbContext.newPage();
  await kbPage.addInitScript(() => window.sessionStorage.setItem('happyranch.token', 'synthetic'));
  await kbPage.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'networkidle' });
  await kbPage.waitForSelector('#capacity-workers');
  await kbPage.focus('#capacity-workers');
  const tabOrder = [];
  for (let i = 0; i < 8; i += 1) {
    const info = await kbPage.evaluate(() => {
      const el = document.activeElement;
      const style = el ? getComputedStyle(el) : null;
      return {
        tag: el?.tagName ?? null,
        id: el?.id || null,
        text: (el?.textContent ?? '').trim().slice(0, 40) || null,
        outline: style?.outlineStyle ?? null,
        outlineWidth: style?.outlineWidth ?? null,
        boxShadow: style?.boxShadow ?? null,
      };
    });
    tabOrder.push(info);
    await kbPage.keyboard.press('Tab');
  }
  await kbPage.screenshot({ path: join(outDir, 'capacity-keyboard-focus-1440x1000-light.png'), fullPage: true });
  await kbContext.close();

  const hashes = readdirSync(outDir).filter((f) => f.endsWith('.png')).sort().map((f) => ({
    file: f,
    sha256: createHash('sha256').update(readFileSync(join(outDir, f))).digest('hex'),
  }));

  const manifest = {
    disclosure: 'SYNTHETIC INTERCEPTED HTTP — frontend behaviour only. Not evidence of backend persistence, audit, restart or deployment.',
    origin,
    undeclaredApiPaths: undeclared,
    blockedExternalRequests: blockedExternal,
    // Fail-closed means NO undeclared `/api/` path was served. Blocked external
    // requests are the guard WORKING, not a failure — they are enumerated above
    // so the reviewer can see exactly what the app attempted to reach.
    failClosedOk: undeclared.length === 0,
    horizontalOverflowStates: results.filter((r) => r.horizontalOverflow).map((r) => r.file),
    states: results,
    tabOrder,
    pngs: hashes,
  };
  writeFileSync(join(outDir, 'MANIFEST.json'), `${JSON.stringify(manifest, null, 2)}\n`);

  console.log(`captured ${hashes.length} PNGs -> ${outDir}`);
  console.log(`undeclared /api/ paths: ${undeclared.length}`);
  console.log(`blocked external requests: ${blockedExternal.length}`);
  console.log(`horizontal overflow states: ${manifest.horizontalOverflowStates.length}`);
  if (undeclared.length > 0) {
    console.error('FAIL: the venue was not fail-closed:', undeclared);
    process.exitCode = 1;
  }
} finally {
  await browser.close();
  await server.close();
}
