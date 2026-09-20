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

/**
 * 16.10 — computed contrast in a REAL browser.
 *
 * WCAG 2.x relative luminance and contrast ratio, measured against the element's
 * EFFECTIVE background (the nearest ancestor with a non-transparent
 * background-color), not an assumed page colour. Reported per state, viewport
 * and theme; nothing here is asserted from a design token or a DOM presence.
 */
const CONTRAST_TARGETS = [
  '#capacity-workers-help', '#capacity-cap-help',
  '#capacity-workers-guidance', '#capacity-cap-guidance',
  '#capacity-reason-help',
  '#capacity-workers-error', '#capacity-cap-error',
  '#capacity-reason-error', '#capacity-ack-error',
];

const CONTRAST_FN = (selectors) => {
  const channel = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  // This app's theme is authored in `oklch()`, and Chrome returns the computed
  // value in that space — an `rgba(...)` regex parses NOTHING here. Resolve any
  // CSS colour through a canvas so the measurement is on real sRGB pixels.
  const ctx2d = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  ctx2d.globalCompositeOperation = 'copy';
  const parse = (value) => {
    if (!value || value === 'none') return null;
    ctx2d.fillStyle = 'rgba(0, 0, 0, 0)';
    ctx2d.fillStyle = value;
    ctx2d.fillRect(0, 0, 1, 1);
    const d = ctx2d.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const effectiveBackground = (node) => {
    let el = node;
    while (el) {
      const bg = parse(getComputedStyle(el).backgroundColor);
      if (bg && bg.a > 0) return bg;
      el = el.parentElement;
    }
    return parse(getComputedStyle(document.body).backgroundColor) ?? { r: 255, g: 255, b: 255, a: 1 };
  };
  const out = [];
  for (const selector of selectors) {
    const el = document.querySelector(selector);
    if (!el) { out.push({ selector, present: false }); continue; }
    const style = getComputedStyle(el);
    const fg = parse(style.color);
    const bg = effectiveBackground(el);
    if (!fg || !bg) { out.push({ selector, present: true, unmeasurable: style.color }); continue; }
    const px = parseFloat(style.fontSize);
    const bold = Number(style.fontWeight) >= 700;
    // WCAG "large text": >=24px, or >=18.66px when bold.
    const large = px >= 24 || (bold && px >= 18.66);
    out.push({
      selector,
      present: true,
      text: (el.textContent ?? '').trim().slice(0, 60),
      color: style.color,
      background: `rgb(${bg.r}, ${bg.g}, ${bg.b})`,
      fontSizePx: px,
      large,
      ratio: Number(ratio(fg, bg).toFixed(2)),
      threshold: large ? 3 : 4.5,
      passesAA: ratio(fg, bg) >= (large ? 3 : 4.5),
    });
  }
  return out;
};

/**
 * 16.11 — walk the 16.6 tab order and measure the REAL focus ring.
 *
 * Two corrections a first pass got wrong, both of which produced a vacuous
 * "every control has a ring":
 *
 *   1. `page.focus()` is PROGRAMMATIC focus. Chrome does not match
 *      `:focus-visible` for it, so the ring variables stay at their
 *      transparent defaults and nothing is measured. The walk therefore starts
 *      at `document.body` and Tabs in, so every reading is keyboard-driven.
 *   2. This design system draws the ring with `box-shadow`, and an UNFOCUSED
 *      control still reports `box-shadow: rgba(0,0,0,0) 0 0 0 0`. Treating
 *      `boxShadow !== 'none'` as a visible ring scores the absent ring as
 *      present. A layer counts only when it has non-zero alpha AND non-zero
 *      spread or blur.
 */
const RING_PROBE = () => {
  const channel = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const ctx2d = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  ctx2d.globalCompositeOperation = 'copy';
  const parse = (value) => {
    if (!value || value === 'none') return null;
    ctx2d.fillStyle = 'rgba(0, 0, 0, 0)';
    ctx2d.fillStyle = value;
    ctx2d.fillRect(0, 0, 1, 1);
    const d = ctx2d.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
  };
  const lum = (c) => 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  /** Composite a translucent ring over what is actually behind it. */
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  // Split a computed box-shadow into layers without breaking on rgb() commas.
  const layers = (value) => {
    if (!value || value === 'none') return [];
    const out = [];
    let depth = 0; let cur = '';
    for (const ch of value) {
      if (ch === '(') depth += 1;
      if (ch === ')') depth -= 1;
      if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  };

  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const style = getComputedStyle(el);
  let surround = el.parentElement;
  while (surround) {
    const bg = parse(getComputedStyle(surround).backgroundColor);
    if (bg && bg.a > 0) break;
    surround = surround.parentElement;
  }
  const surroundBg = parse(getComputedStyle(surround ?? document.body).backgroundColor)
    ?? { r: 255, g: 255, b: 255, a: 1 };

  const outlineWidth = parseFloat(style.outlineWidth) || 0;
  const outlineReal = outlineWidth > 0 && style.outlineStyle !== 'none';

  // A ring layer must actually paint: visible alpha AND a non-zero spread/blur.
  const ringLayers = layers(style.boxShadow).map((layer) => {
    const colour = parse((/(rgba?\([^)]*\)|#[0-9a-f]{3,8})/i.exec(layer) ?? [])[0]);
    const lengths = (layer.match(/-?[\d.]+px/g) ?? []).map(parseFloat);
    const paints = !!colour && colour.a > 0.05 && lengths.some((n) => Math.abs(n) > 0);
    return { layer, colour, paints };
  }).filter((l) => l.paints);

  const ringSource = ringLayers.length > 0
    ? ringLayers[ringLayers.length - 1].colour
    : (outlineReal ? parse(style.outlineColor) : null);

  return {
    tag: el.tagName,
    id: el.id || null,
    text: (el.textContent ?? '').trim().slice(0, 40) || null,
    focusVisible: el.matches(':focus-visible'),
    outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
    boxShadow: style.boxShadow,
    paintedRingLayers: ringLayers.map((l) => l.layer),
    // The honest predicate: keyboard focus is actually matched AND something
    // actually paints.
    focusRingVisible: el.matches(':focus-visible') && (outlineReal || ringLayers.length > 0),
    // The translucent ring composited over what is behind it, against that
    // same background — what a person actually has to see.
    ringContrast: ringSource
      ? Number(ratio(over(ringSource, surroundBg), surroundBg).toFixed(2))
      : null,
  };
};

/**
 * Tab in from the document start so focus is keyboard-driven, stop on the first
 * capacity control, then record the panel's tab order.
 */
async function walkTabOrder(page) {
  await page.evaluate(() => document.body.focus());
  let reached = false;
  for (let i = 0; i < 80 && !reached; i += 1) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(() => document.activeElement?.id === 'capacity-workers');
  }
  if (!reached) throw new Error('never tabbed to #capacity-workers');
  const order = [];
  for (let i = 0; i < 7; i += 1) {
    order.push(await page.evaluate(RING_PROBE));
    if (i < 6) await page.keyboard.press('Tab');
  }
  return order;
}

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
        const contrast = await page.evaluate(CONTRAST_FN, CONTRAST_TARGETS);
        results.push({
          state: state.name, viewport: viewport.name, theme, file, ...receipt, contrast,
        });
        await page.close();
      }
      await context.close();
    }
  }

  // 16.11 — keyboard/focus pass in a real browser at BOTH desktop widths and in
  // BOTH themes. A single light/1440 pass could not see a ring that disappears
  // in dark or a tab order that changes when the shell reflows.
  const tabOrders = [];
  for (const viewport of VIEWPORTS) {
    for (const theme of THEMES) {
      const kbContext = await browser.newContext({
        viewport: { width: viewport.width, height: viewport.height },
        colorScheme: theme,
        deviceScaleFactor: 1,
      });
      await kbContext.route('**/*', (route) => (route.request().url().startsWith(origin)
        ? route.continue()
        : (blockedExternal.push(route.request().url()), route.abort())));
      currentCapacity = () => snapshot();
      const kbPage = await kbContext.newPage();
      await kbPage.addInitScript((t) => {
        window.sessionStorage.setItem('happyranch.token', 'synthetic');
        window.localStorage.setItem('happyranch.theme', t);
      }, theme);
      await kbPage.goto(`${origin}/orgs/${SLUG}/settings/daemon-capacity`, { waitUntil: 'networkidle' });
      await kbPage.waitForSelector('#capacity-workers');
      const order = await walkTabOrder(kbPage);
      // Still keyboard-focused on the LAST control of the walk; re-focusing
      // programmatically here would erase the ring from the screenshot.
      await kbPage.screenshot({
        path: join(outDir, `capacity-keyboard-focus-${viewport.name}-${theme}.png`),
        fullPage: true,
      });
      tabOrders.push({ viewport: viewport.name, theme, order });
      await kbContext.close();
    }
  }

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
    tabOrders,
    // 16.10 / 16.11 roll-ups. Empty arrays are the passing result.
    lowContrastFindings: results.flatMap((r) => (r.contrast ?? [])
      .filter((c) => c.present && (c.unmeasurable !== undefined || !c.passesAA))
      .map((c) => ({ file: r.file, ...c }))),
    focusRingsMissing: tabOrders.flatMap((t) => t.order
      .filter((o) => o && (!o.focusVisible || !o.focusRingVisible))
      .map((o) => ({
        viewport: t.viewport, theme: t.theme, control: o.id ?? o.text,
        focusVisible: o.focusVisible, boxShadow: o.boxShadow, outline: o.outline,
      }))),
    // Reported, not gated: a ring can paint and still be hard to see.
    lowContrastFocusRings: tabOrders.flatMap((t) => t.order
      .filter((o) => o && o.focusRingVisible && o.ringContrast !== null && o.ringContrast < 3)
      .map((o) => ({
        viewport: t.viewport, theme: t.theme, control: o.id ?? o.text, ratio: o.ringContrast,
      }))),
    pngs: hashes,
  };
  writeFileSync(join(outDir, 'MANIFEST.json'), `${JSON.stringify(manifest, null, 2)}\n`);

  console.log(`captured ${hashes.length} PNGs -> ${outDir}`);
  console.log(`undeclared /api/ paths: ${undeclared.length}`);
  console.log(`blocked external requests: ${blockedExternal.length}`);
  console.log(`horizontal overflow states: ${manifest.horizontalOverflowStates.length}`);
  console.log(`low-contrast findings (16.10): ${manifest.lowContrastFindings.length}`);
  console.log(`controls without a visible focus ring (16.11): ${manifest.focusRingsMissing.length}`);
  console.log(`focus rings below 3:1 against their surround (16.11, reported): ${manifest.lowContrastFocusRings.length}`);
  if (undeclared.length > 0) {
    console.error('FAIL: the venue was not fail-closed:', undeclared);
    process.exitCode = 1;
  }
} finally {
  await browser.close();
  await server.close();
}
