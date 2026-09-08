/**
 * THR-230 / TASK-6925 — AppShell sidebar viewport-height evidence harness.
 *
 * Proves the acceptance criteria for the bounded sidebar viewport-height
 * correction in `design-system/layouts/AppShell/Sidebar.tsx`:
 *
 *   - the outer <aside> stays inside the visible viewport at short laptop
 *     heights (1280x800 AND 1280x600 CSS content viewports),
 *   - overflow nav links are reachable by internal scroll AND by keyboard,
 *   - the footer Settings item and the account row stay reachable,
 *   - long routed main-page content scrolls inside <main> without expanding
 *     the sidebar or the document,
 *   - resize 800 -> 600 -> 800 and across the `md` (768px) boundary is stable,
 *   - the collapsed 56px icon rail at 390x844 / 390x600 is unchanged,
 *   - the org-switcher popover is not clipped by the new scroll container.
 *
 * The assertions are written so they FAIL against the pre-fix build. Run the
 * same script twice to produce the before/after ledger (MEM-113):
 *
 *   node scripts/screenshot-harness/shot-thr230-sidebar-viewport.mjs \
 *     --dist=/abs/path/to/baseline-dist --out=/abs/out/before --label=before
 *   node scripts/screenshot-harness/shot-thr230-sidebar-viewport.mjs \
 *     --dist=web/dist --out=/abs/out/after --label=after
 *
 * Exit code is 0 only when every assertion for the run passes AND the
 * fail-closed mock recorded zero route violations. `--label=before` inverts
 * the exit expectation (the baseline is EXPECTED to fail) but still writes
 * the full ledger.
 *
 * Guardrails honoured (see web/scripts/screenshot-harness/README.md):
 *   - no npm dependency; Node built-ins + the venv `playwright-cli` binary,
 *   - zero Web-CI footprint (plain .mjs under web/scripts/, outside
 *     eslint/tsc/vite graphs),
 *   - the /api mock is FAIL-CLOSED (MEM-172): an unlisted route returns 501
 *     and is recorded as a violation instead of a silent `{}`.
 */

import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer as httpCreateServer } from 'node:http';
import { existsSync, readdirSync, statSync, lstatSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, '..', '..');

const MIME = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
  '.map': 'application/json',
  '.ico': 'image/x-icon',
};

const ORG = 'demo';
const OTHER_ORG = 'other';
const EXPECTED_NAV_LINKS = 14;

// ---------------------------------------------------------------------------
// args
// ---------------------------------------------------------------------------

function arg(name, fallback) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
}

const DIST = resolve(arg('dist', join(WEB_ROOT, 'dist')));
const OUT = resolve(arg('out', join(HERE, 'out', 'thr230')));
const LABEL = arg('label', 'after');
const SESSION = arg('session', 'thr230');
const RUN_NONCE = createHash('sha256')
  .update(`${Date.now()}:${process.pid}:${LABEL}`)
  .digest('hex')
  .slice(0, 16);

// ---------------------------------------------------------------------------
// fail-closed static + /api mock
// ---------------------------------------------------------------------------

/**
 * Walk the pinned dist tree and build the exact static allowlist. Throws on a
 * symlink so a swapped-in artifact cannot smuggle content from outside `dist`.
 */
function staticAllowlist(root) {
  const allowed = new Set();
  const walk = (dir, prefix) => {
    for (const entry of readdirSync(dir)) {
      const abs = join(dir, entry);
      if (lstatSync(abs).isSymbolicLink()) {
        throw new Error(`refusing to serve symlink inside dist: ${abs}`);
      }
      if (statSync(abs).isDirectory()) walk(abs, `${prefix}${entry}/`);
      else allowed.add(`${prefix}${entry}`);
    }
  };
  walk(root, '');
  return allowed;
}

/**
 * Shell data states. The sidebar itself consumes exactly two reads — the org
 * list (`useOrgsList`) and the dashboard summary's `org_age_days`
 * (`useDashboardSummary`) — so these are the states that can move its layout.
 * `state` is flipped from the harness through `GET /__state?name=`.
 */
const SHELL_STATES = ['populated', 'empty', 'loading', 'error', 'no-org', 'long-content'];
let SHELL_STATE = 'populated';

/**
 * Unique text carried by the LAST row of the `long-content` dashboard payload.
 * `<main>` is `overflow-hidden`, so appending a synthetic tall div to it proves
 * nothing about reachability. Instead the mock serves a genuinely long REAL
 * payload and the harness locates this marker in the REAL routed DOM, so
 * "the last content is reachable" is measured on actual routed content inside
 * its actual scroller.
 */
const LAST_ROW_MARKER = 'thr230-last-activity-row';
const LONG_ACTIVITY_ROWS = 60;

/** A genuinely long — and schema-valid — dashboard summary. */
function longSummaryBody() {
  const body = summaryBody(42);
  body.recent_activity = Array.from({ length: LONG_ACTIVITY_ROWS }, (_, i) => ({
    timestamp: `2026-09-07T${String(11 - (i % 12)).padStart(2, '0')}:00:00Z`,
    who: i === LONG_ACTIVITY_ROWS - 1 ? LAST_ROW_MARKER : `thr230-agent-${i}`,
    event_kind: 'task_completed',
    task_id: null,
    verdict: null,
  }));
  return body;
}

function summaryBody(orgAgeDays) {
  return {
    heartbeat: [],
    narrative_counts: {},
    escalations: [],
    pending_review_jobs: [],
    active_by_team: [],
    recent_activity: [],
    updates_this_week: [],
    org_pulse: [],
    org_age_days: orgAgeDays,
    server_now: '2026-09-07T12:00:00Z',
    generated_at: '2026-09-07T12:00:00Z',
  };
}

/**
 * Exact `METHOD pathname[?query]` allowlist. Each entry is a resolver over the
 * current shell state and returns `{ status, json }`; anything unlisted is a
 * recorded violation (fail-closed, MEM-172).
 */
const API_ALLOWLIST = new Map(
  Object.entries({
    'GET /api/v1/auth/bootstrap': () => ({ status: 200, json: { token: `thr230-${RUN_NONCE}` } }),
    'GET /api/v1/orgs': () => ({
      status: 200,
      json: {
        orgs:
          SHELL_STATE === 'no-org'
            ? []
            : SHELL_STATE === 'empty'
              ? [{ slug: ORG, root: '/tmp/demo' }]
              : [
                  { slug: ORG, root: '/tmp/demo' },
                  { slug: OTHER_ORG, root: '/tmp/other' },
                ],
        broken: [],
      },
    }),
    [`GET /api/v1/orgs/${ORG}/dashboard/summary`]: () => {
      if (SHELL_STATE === 'error') return { status: 500, json: { detail: 'harness-forced-error' } };
      if (SHELL_STATE === 'loading') return { status: 200, json: summaryBody(42), delayMs: 60_000 };
      if (SHELL_STATE === 'long-content') return { status: 200, json: longSummaryBody() };
      return { status: 200, json: summaryBody(SHELL_STATE === 'empty' ? 0 : 42) };
    },
    [`GET /api/v1/orgs/${OTHER_ORG}/dashboard/summary`]: () => ({ status: 200, json: summaryBody(7) }),
  }),
);

/**
 * Routes whose query carries a run-varying value (a `now`-derived ISO instant)
 * cannot be pinned to a literal query string. They are declared here as an
 * EXACT parameter contract instead — the exact allowed parameter names, the
 * exact required subset, and a per-parameter value validator. A request with an
 * extra, missing, or malformed parameter is still a recorded violation; this is
 * a declared contract, not a widening to "any query" (MEM-172).
 */
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/;
const API_QUERY_CONTRACTS = new Map(
  Object.entries({
    [`GET /api/v1/orgs/${ORG}/tokens`]: {
      allowed: ['group_by', 'since'],
      required: ['group_by', 'since'],
      validate: {
        group_by: (v) => v === 'agent' || v === 'thread',
        since: (v) => ISO_INSTANT.test(v),
      },
      resolve: () => ({ status: 200, json: { rollup: [] } }),
    },
    [`GET /api/v1/orgs/${OTHER_ORG}/tokens`]: {
      allowed: ['group_by', 'since'],
      required: ['group_by', 'since'],
      validate: {
        group_by: (v) => v === 'agent' || v === 'thread',
        since: (v) => ISO_INSTANT.test(v),
      },
      resolve: () => ({ status: 200, json: { rollup: [] } }),
    },
  }),
);

/** Returns a resolver when `search` satisfies the declared contract, else null. */
function matchQueryContract(bare, searchParams) {
  const contract = API_QUERY_CONTRACTS.get(bare);
  if (!contract) return null;
  const names = [...searchParams.keys()];
  if (names.some((n) => !contract.allowed.includes(n))) return null;
  if (contract.required.some((n) => !searchParams.has(n))) return null;
  for (const [n, ok] of Object.entries(contract.validate)) {
    if (searchParams.has(n) && !ok(searchParams.get(n))) return null;
  }
  return contract.resolve;
}

/**
 * Requests Chromium emits on its own that are NOT app assets and are NOT in
 * `dist`. They are still REFUSED (404) — they are listed only so a browser
 * default is not mis-reported as an app-side allowlist violation. The app's
 * real icon is `/happyranch-favicon.svg`, which IS in dist.
 */
const BROWSER_DEFAULT_PATHS = new Set(['favicon.ico']);

async function startServer() {
  const staticFiles = staticAllowlist(DIST);
  const violations = [];
  const browserDefaultRefusals = [];
  const served = [];

  const server = httpCreateServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const pathname = url.pathname;

    // Harness-only state control (never an app route).
    if (pathname === '/__state') {
      const name = url.searchParams.get('name');
      if (!SHELL_STATES.includes(name)) {
        res.writeHead(400);
        res.end('unknown state');
        return;
      }
      SHELL_STATE = name;
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ state: SHELL_STATE }));
      return;
    }

    if (pathname.startsWith('/api/')) {
      const key = `${req.method} ${pathname}${url.search}`;
      const bare = `${req.method} ${pathname}`;
      const resolve_ =
        API_ALLOWLIST.get(key) ??
        API_ALLOWLIST.get(bare) ??
        matchQueryContract(bare, url.searchParams) ??
        undefined;
      if (resolve_ === undefined) {
        violations.push(key);
        res.writeHead(501, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'route_not_in_allowlist', key }));
        return;
      }
      const r = resolve_(url.searchParams);
      served.push(`${bare} -> ${r.status}${r.delayMs ? ` (delay ${r.delayMs}ms)` : ''} [${SHELL_STATE}]`);
      const send = () => {
        res.writeHead(r.status, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(r.json));
      };
      if (r.delayMs) setTimeout(send, r.delayMs);
      else send();
      return;
    }

    // Static: exact allowlist from the pinned dist tree, else SPA fallback.
    const rel = decodeURIComponent(pathname).replace(/^\/+/, '');
    const target = staticFiles.has(rel) ? rel : 'index.html';
    if (rel !== target && extname(rel)) {
      if (BROWSER_DEFAULT_PATHS.has(rel)) browserDefaultRefusals.push(rel);
      else violations.push(`STATIC ${rel}`);
      res.writeHead(404);
      res.end('not in dist allowlist');
      return;
    }
    const file = join(DIST, target);
    const data = await readFile(file);
    res.writeHead(200, { 'Content-Type': MIME[extname(file)] || 'application/octet-stream' });
    res.end(data);
  });

  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    violations,
    browserDefaultRefusals,
    served,
    close: () => new Promise((r) => server.close(() => r())),
  };
}

// ---------------------------------------------------------------------------
// playwright-cli driver (one persistent session across a whole scenario)
// ---------------------------------------------------------------------------

function pw(args) {
  return new Promise((res, rej) => {
    const proc = spawn('playwright-cli', [`-s=${SESSION}`, '--raw', ...args], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    let err = '';
    proc.stdout.on('data', (d) => (out += d));
    proc.stderr.on('data', (d) => (err += d));
    proc.on('exit', (code) =>
      code === 0
        ? res(out.trim())
        : rej(new Error(`playwright-cli ${args[0]} failed (${code}): ${err || out}`)),
    );
    proc.on('error', rej);
  });
}

/** `--raw eval` returns the value JSON-encoded once more (MEM-096). */
async function evaluate(fn) {
  const raw = await pw(['eval', fn]);
  const line = raw.split('\n').filter(Boolean).pop();
  return JSON.parse(JSON.parse(line));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// in-page probes (stringified — they run in the browser)
// ---------------------------------------------------------------------------

const READY_PROBE = `() => {
  const aside = document.querySelector('aside[role="navigation"]');
  const nav = aside && aside.querySelector('nav[aria-label="Primary navigation items"]');
  const items = nav ? nav.querySelectorAll('a, span[aria-disabled="true"]') : [];
  const account = document.querySelector('[aria-label^="Account:"]');
  const errorish = /something went wrong|failed to load/i.test(document.body.innerText || '');
  return JSON.stringify({
    ready0: !!(aside && nav && account) && items.length === ${EXPECTED_NAV_LINKS},
    ready: !!(aside && nav && account) && items.length === ${EXPECTED_NAV_LINKS} && !errorish,
    navItems: items.length,
    theme: document.documentElement.getAttribute('data-theme') || 'light',
    errorish,
  });
}`;

/**
 * The geometry ledger. Reports the outer aside, its scroll region, the footer
 * controls, the shell ancestors, <main>, and the document scroller — plus a
 * full ancestor-chain clipping walk for the two footer controls and the last
 * nav link (KB adversarial-browser-evidence-harness-requirements §2).
 */
const GEOMETRY_PROBE = `() => {
  const box = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      tag: el.tagName.toLowerCase(),
      className: (el.getAttribute('class') || ''),
      rect: { top: +b.top.toFixed(2), bottom: +b.bottom.toFixed(2), left: +b.left.toFixed(2), right: +b.right.toFixed(2), width: +b.width.toFixed(2), height: +b.height.toFixed(2) },
      clientHeight: el.clientHeight, scrollHeight: el.scrollHeight, scrollTop: el.scrollTop,
      clientWidth: el.clientWidth,
      computed: { overflowY: cs.overflowY, overflowX: cs.overflowX, minHeight: cs.minHeight, height: cs.height, flex: cs.flex, position: cs.position, marginTop: cs.marginTop },
    };
  };
  // Walk every ancestor and report the ones that can clip.
  const clipChain = (el) => {
    const out = [];
    let n = el && el.parentElement;
    while (n) {
      const cs = getComputedStyle(n);
      if (/hidden|scroll|auto|clip/.test(cs.overflowY) || /hidden|scroll|auto|clip/.test(cs.overflowX)) {
        const b = n.getBoundingClientRect();
        out.push({ tag: n.tagName.toLowerCase(), className: (n.getAttribute('class') || '').slice(0, 120),
                   overflowY: cs.overflowY, overflowX: cs.overflowX,
                   rect: { top: +b.top.toFixed(2), bottom: +b.bottom.toFixed(2), left: +b.left.toFixed(2), right: +b.right.toFixed(2) } });
      }
      n = n.parentElement;
    }
    return out;
  };
  // Fully visible = inside the viewport AND inside every clipping ancestor.
  const visibility = (el) => {
    if (!el) return null;
    const b = el.getBoundingClientRect();
    let clippedBy = null;
    for (const a of clipChain(el)) {
      if (b.top < a.rect.top - 0.5 || b.bottom > a.rect.bottom + 0.5 || b.left < a.rect.left - 0.5 || b.right > a.rect.right + 0.5) { clippedBy = a; break; }
    }
    const inViewport = b.top >= -0.5 && b.bottom <= innerHeight + 0.5 && b.left >= -0.5 && b.right <= innerWidth + 0.5;
    return { inViewport, clippedBy, rect: { top: +b.top.toFixed(2), bottom: +b.bottom.toFixed(2) } };
  };

  const aside = document.querySelector('aside[role="navigation"]');
  const nav = aside && aside.querySelector('nav[aria-label="Primary navigation items"]');
  const orgSection = aside && aside.querySelector('section[aria-label="Organization switcher"]');
  const footer = aside && aside.lastElementChild && aside.lastElementChild.querySelector
    ? [...aside.children].find((c) => c.tagName === 'DIV' && c.querySelector('[aria-label^="Account:"]'))
    : null;
  const shell = aside && aside.parentElement;
  const main = document.querySelector('main');
  const de = document.documentElement;
  const navItems = nav ? [...nav.querySelectorAll('a, span[aria-disabled="true"]')] : [];
  const lastNavItem = navItems[navItems.length - 1] || null;
  const settings = footer ? footer.querySelector('a, span[aria-disabled="true"]') : null;
  const account = document.querySelector('[aria-label^="Account:"]');

  return JSON.stringify({
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    theme: de.getAttribute('data-theme') || 'light',
    document: {
      clientHeight: de.clientHeight, scrollHeight: de.scrollHeight,
      bodyScrollHeight: document.body.scrollHeight, scrollY: window.scrollY,
      htmlOverflowY: getComputedStyle(de).overflowY,
      bodyOverflowY: getComputedStyle(document.body).overflowY,
      rootHeight: +document.getElementById('root').getBoundingClientRect().height.toFixed(2),
      rootScrollHeight: document.getElementById('root').scrollHeight,
    },
    shell: box(shell), aside: box(aside), orgSection: box(orgSection),
    nav: box(nav), footer: box(footer), main: box(main),
    navItemCount: navItems.length,
    lastNavItem: lastNavItem ? { label: lastNavItem.textContent.trim(), ...box(lastNavItem), visibility: visibility(lastNavItem) } : null,
    settings: settings ? { label: settings.textContent.trim(), ...box(settings), visibility: visibility(settings) } : null,
    account: account ? { ...box(account), visibility: visibility(account) } : null,
    asideVisibility: visibility(aside),
    asideClipChain: clipChain(aside),
  });
}`;

/** Keyboard traversal: Tab from the org switcher until every target is hit. */
const KEYBOARD_PROBE = `() => {
  const aside = document.querySelector('aside[role="navigation"]');
  const nav = aside.querySelector('nav[aria-label="Primary navigation items"]');
  const items = [...nav.querySelectorAll('a, span[aria-disabled="true"]')];
  const last = items[items.length - 1];
  const footer = [...aside.children].find((c) => c.tagName === 'DIV' && c.querySelector('[aria-label^="Account:"]'));
  const settings = footer.querySelector('a, span[aria-disabled="true"]');
  const account = document.querySelector('[aria-label^="Account:"]');
  const a = document.activeElement;
  const where = (el) => {
    const b = el.getBoundingClientRect();
    const nb = nav.getBoundingClientRect();
    return { top: +b.top.toFixed(2), bottom: +b.bottom.toFixed(2),
             insideViewport: b.top >= -0.5 && b.bottom <= innerHeight + 0.5,
             insideNavScrollport: b.top >= nb.top - 0.5 && b.bottom <= nb.bottom + 0.5,
             // Clearance to the scrollport edges. The focus ring is a 2px
             // box-shadow drawn OUTSIDE the border box, so a 0px gap at an
             // edge means the ring is clipped there.
             gapToScrollportTop: +(b.top - nb.top).toFixed(2),
             gapToScrollportBottom: +(nb.bottom - b.bottom).toFixed(2),
             navIsScrollable: nav.scrollHeight > nav.clientHeight + 1 };
  };
  return JSON.stringify({
    activeLabel: a ? (a.getAttribute('aria-label') || a.textContent.trim()).slice(0, 40) : null,
    activeIsLastNavItem: a === last,
    activeIsSettings: a === settings,
    activeIsAccount: a === account,
    navScrollTop: nav.scrollTop, navScrollHeight: nav.scrollHeight, navClientHeight: nav.clientHeight,
    documentScrollY: window.scrollY,
    active: a && a !== document.body ? where(a) : null,
  });
}`;

// ---------------------------------------------------------------------------
// assertion helper
// ---------------------------------------------------------------------------

function makeChecks() {
  const results = [];
  return {
    results,
    check(id, criterion, pass, detail) {
      results.push({ id, criterion, pass: !!pass, detail });
      return !!pass;
    },
    get failed() {
      return results.filter((r) => !r.pass);
    },
  };
}

// ---------------------------------------------------------------------------
// scenario driver
// ---------------------------------------------------------------------------

async function openSession(url, { width, height, theme, route, allowPageError = false }) {
  await pw(['open', 'about:blank']);
  await pw(['resize', String(width), String(height)]);
  await pw(['goto', `${url}${route}`]);
  await pw(['localstorage-set', 'happyranch.theme', theme]);
  await pw(['reload']);
  return waitReady({ theme, allowPageError, where: `${width}x${height} ${theme} ${route}` });
}

/**
 * Readiness poll. Requires the real sidebar landmark with its full item
 * inventory, the account row, and the expected `data-theme`. By default it
 * also rejects a routed-content error fallback so a broken page can never be
 * screenshotted as if it were healthy (MEM-172); the deliberate `error` and
 * `loading` shell-state scenarios opt out via `allowPageError`, because there
 * the fallback IS the state under test.
 */
async function waitReady({ theme, allowPageError = false, where = '' }) {
  let last = null;
  for (let i = 0; i < 40; i += 1) {
    await sleep(250);
    last = await evaluate(READY_PROBE);
    const themeOk = theme ? last.theme === theme : true;
    const errOk = allowPageError || !last.errorish;
    if (last.ready0 && errOk && themeOk) return last;
  }
  throw new Error(`readiness poll never satisfied at ${where}: ${JSON.stringify(last)}`);
}

async function shot(file) {
  await mkdir(dirname(file), { recursive: true });
  await pw(['screenshot', `--filename=${file}`]);
  if (!existsSync(file)) throw new Error(`screenshot not written: ${file}`);
  const buf = await readFile(file);
  // PNG IHDR carries the true pixel size — never claim more than it shows
  // (MEM-173).
  const w = buf.readUInt32BE(16);
  const h = buf.readUInt32BE(20);
  return { file, bytes: buf.length, pixels: [w, h], sha256: createHash('sha256').update(buf).digest('hex') };
}

async function run() {
  await rm(OUT, { recursive: true, force: true });
  await mkdir(OUT, { recursive: true });
  const startedAt = Date.now();

  const srv = await startServer();
  const checks = makeChecks();
  const ledger = [];
  const shots = [];
  const cleanupErrors = [];
  let fatal = null;

  const VIEWPORTS = [
    { name: 'laptop-1280x800', width: 1280, height: 800, rail: 'wide' },
    { name: 'laptop-1280x600', width: 1280, height: 600, rail: 'wide' },
    { name: 'mobile-390x844', width: 390, height: 844, rail: 'narrow' },
    { name: 'mobile-390x600', width: 390, height: 600, rail: 'narrow' },
  ];

  try {
    // ---- 1. geometry + screenshots, every viewport x both themes ----------
    for (const vp of VIEWPORTS) {
      for (const theme of ['light', 'dark']) {
        await openSession(srv.url, { ...vp, theme, route: `/orgs/${ORG}/dashboard` });
        const g = await evaluate(GEOMETRY_PROBE);
        ledger.push({ scenario: `${vp.name}-${theme}`, geometry: g });
        shots.push({ scenario: `${vp.name}-${theme}`, ...(await shot(join(OUT, `sidebar-${vp.name}-${theme}.png`))) });

        const tag = `${vp.name}/${theme}`;
        checks.check(
          `aside-inside-viewport:${tag}`,
          'Outer sidebar stays inside the visible viewport',
          g.aside.rect.bottom <= g.viewport.height + 0.5 && g.aside.rect.top >= -0.5,
          { asideBottom: g.aside.rect.bottom, viewportHeight: g.viewport.height },
        );
        checks.check(
          `no-document-scroll:${tag}`,
          'Sidebar does not expand the document scroller',
          g.document.scrollHeight <= g.document.clientHeight + 0.5,
          { docScrollHeight: g.document.scrollHeight, docClientHeight: g.document.clientHeight },
        );
        checks.check(
          `footer-settings-reachable:${tag}`,
          'Footer Settings control is inside the viewport and unclipped',
          g.settings && g.settings.visibility.inViewport && !g.settings.visibility.clippedBy,
          g.settings && g.settings.visibility,
        );
        checks.check(
          `footer-account-reachable:${tag}`,
          'Footer account row is inside the viewport and unclipped',
          g.account && g.account.visibility.inViewport && !g.account.visibility.clippedBy,
          g.account && g.account.visibility,
        );
        checks.check(
          `nav-item-count:${tag}`,
          'Navigation order/inventory preserved (14 items)',
          g.navItemCount === EXPECTED_NAV_LINKS,
          { navItemCount: g.navItemCount },
        );
        checks.check(
          `rail-width:${tag}`,
          'Rail width preserved (244px wide / 56px collapsed)',
          Math.abs(g.aside.rect.width - (vp.rail === 'wide' ? 244 : 56)) < 0.6,
          { width: g.aside.rect.width, expected: vp.rail === 'wide' ? 244 : 56 },
        );
      }
    }

    // ---- 2. short-viewport interaction proof (the acceptance core) --------
    await openSession(srv.url, { width: 1280, height: 600, theme: 'light', route: `/orgs/${ORG}/dashboard` });
    const short = await evaluate(GEOMETRY_PROBE);
    checks.check(
      'overflow-is-internal:1280x600',
      'Nav overflow is carried by an internal scroll region, not the document',
      short.nav.computed.overflowY === 'auto' &&
        short.nav.scrollHeight > short.nav.clientHeight &&
        short.document.scrollHeight <= short.document.clientHeight + 0.5,
      {
        navOverflowY: short.nav.computed.overflowY,
        navScrollHeight: short.nav.scrollHeight,
        navClientHeight: short.nav.clientHeight,
        docScrollHeight: short.document.scrollHeight,
      },
    );

    // scroll the nav to its end -> the last link becomes fully visible
    await evaluate(`() => { const n = document.querySelector('nav[aria-label="Primary navigation items"]'); n.scrollTop = n.scrollHeight; return JSON.stringify({ scrollTop: n.scrollTop }); }`);
    await sleep(250);
    const scrolled = await evaluate(GEOMETRY_PROBE);
    ledger.push({ scenario: 'laptop-1280x600-light-nav-scrolled-to-end', geometry: scrolled });
    shots.push({
      scenario: 'laptop-1280x600-light-nav-scrolled-to-end',
      ...(await shot(join(OUT, 'sidebar-laptop-1280x600-light-nav-scrolled.png'))),
    });
    checks.check(
      'last-link-reachable-by-scroll:1280x600',
      'The last overflow nav link is reachable by internal scroll',
      scrolled.lastNavItem.visibility.inViewport && !scrolled.lastNavItem.visibility.clippedBy,
      { label: scrolled.lastNavItem.label, visibility: scrolled.lastNavItem.visibility },
    );
    checks.check(
      'scroll-does-not-move-document:1280x600',
      'Scrolling the nav leaves the document scroll position at 0',
      scrolled.document.scrollY === 0 && scrolled.document.scrollHeight <= scrolled.document.clientHeight + 0.5,
      { scrollY: scrolled.document.scrollY, docScrollHeight: scrolled.document.scrollHeight },
    );
    checks.check(
      'footer-static-while-nav-scrolls:1280x600',
      'The footer does not move while the nav scrolls',
      Math.abs(scrolled.footer.rect.top - short.footer.rect.top) < 0.6,
      { before: short.footer.rect.top, after: scrolled.footer.rect.top },
    );

    // keyboard traversal from the org switcher through every nav item to the
    // footer controls
    await evaluate(`() => { const n = document.querySelector('nav[aria-label="Primary navigation items"]'); n.scrollTop = 0; document.querySelector('aside[role="navigation"] button').focus(); return JSON.stringify({ ok: true }); }`);
    const keyboard = { steps: [] };
    let sawLast = null;
    let sawSettings = null;
    let sawAccount = null;
    for (let i = 0; i < EXPECTED_NAV_LINKS + 4; i += 1) {
      await pw(['press', 'Tab']);
      const k = await evaluate(KEYBOARD_PROBE);
      keyboard.steps.push(k);
      if (k.activeIsLastNavItem) sawLast = k;
      if (k.activeIsSettings) sawSettings = k;
      if (k.activeIsAccount) sawAccount = k;
      if (sawAccount) break;
    }
    ledger.push({ scenario: 'laptop-1280x600-light-keyboard', keyboard });
    shots.push({
      scenario: 'laptop-1280x600-light-keyboard-account-focused',
      ...(await shot(join(OUT, 'sidebar-laptop-1280x600-light-keyboard.png'))),
    });
    checks.check(
      'keyboard-reaches-last-nav-item:1280x600',
      'Keyboard Tab reaches the last overflow nav link and scrolls it into view',
      !!sawLast && sawLast.active.insideViewport && sawLast.active.insideNavScrollport && sawLast.documentScrollY === 0,
      sawLast,
    );
    checks.check(
      'keyboard-reaches-settings:1280x600',
      'Keyboard Tab reaches the footer Settings control inside the viewport',
      !!sawSettings && sawSettings.active.insideViewport,
      sawSettings,
    );
    checks.check(
      'keyboard-reaches-account:1280x600',
      'Keyboard Tab reaches the footer account row inside the viewport',
      !!sawAccount && sawAccount.active.insideViewport,
      sawAccount,
    );
    checks.check(
      'focus-ring-not-clipped-at-scrollport-edge:1280x600',
      'The focus ring of a nav item scrolled flush against a scrollport edge is not clipped',
      !!sawLast &&
        sawLast.active.navIsScrollable &&
        sawLast.active.gapToScrollportTop >= 2 &&
        sawLast.active.gapToScrollportBottom >= 2,
      sawLast && sawLast.active,
    );
    checks.check(
      'keyboard-never-scrolls-document:1280x600',
      'No Tab step scrolls the document',
      keyboard.steps.every((s) => s.documentScrollY === 0),
      { maxScrollY: Math.max(...keyboard.steps.map((s) => s.documentScrollY)) },
    );

    // ---- 3. org-switcher popover (portal) is not clipped ------------------
    await pw(['click', 'aside[role="navigation"] button[aria-label="Active org"]']);
    await sleep(500);
    const popover = await evaluate(`() => {
      const items = [...document.querySelectorAll('[role="option"]')];
      const nav = document.querySelector('nav[aria-label="Primary navigation items"]');
      const navRect = nav.getBoundingClientRect();
      return JSON.stringify({
        optionCount: items.length,
        labels: items.map((i) => i.textContent.trim()),
        rects: items.map((i) => { const b = i.getBoundingClientRect(); return { top: +b.top.toFixed(2), bottom: +b.bottom.toFixed(2), width: +b.width.toFixed(2) }; }),
        insideViewport: items.every((i) => { const b = i.getBoundingClientRect(); return b.top >= -0.5 && b.bottom <= innerHeight + 0.5; }),
        escapesNavScrollport: items.some((i) => { const b = i.getBoundingClientRect(); return b.right > navRect.right + 0.5 || b.bottom > navRect.bottom + 0.5 || b.top < navRect.top - 0.5; }),
        documentScrollHeight: document.documentElement.scrollHeight,
        documentClientHeight: document.documentElement.clientHeight,
      });
    }`);
    ledger.push({ scenario: 'laptop-1280x600-light-org-switcher-open', popover });
    shots.push({
      scenario: 'laptop-1280x600-light-org-switcher-open',
      ...(await shot(join(OUT, 'sidebar-laptop-1280x600-light-org-switcher.png'))),
    });
    checks.check(
      'org-switcher-not-clipped:1280x600',
      'The org-switcher popover renders unclipped in the viewport',
      popover.optionCount >= 2 && popover.insideViewport,
      popover,
    );
    checks.check(
      'org-switcher-does-not-grow-document:1280x600',
      'Opening the org switcher does not expand the document scroller',
      popover.documentScrollHeight <= popover.documentClientHeight + 0.5,
      popover,
    );
    await pw(['press', 'Escape']);
    await sleep(300);

    // org switch itself — navigate and re-assert the shell still fits
    await pw(['goto', `${srv.url}/orgs/${OTHER_ORG}/dashboard`]);
    await waitReady({ where: 'post-navigation' });
    const switched = await evaluate(GEOMETRY_PROBE);
    ledger.push({ scenario: 'laptop-1280x600-light-org-switched', geometry: switched });
    checks.check(
      'org-switch-preserves-fit:1280x600',
      'After switching org the sidebar still fits the viewport',
      switched.aside.rect.bottom <= switched.viewport.height + 0.5 &&
        switched.document.scrollHeight <= switched.document.clientHeight + 0.5,
      { asideBottom: switched.aside.rect.bottom, docScrollHeight: switched.document.scrollHeight },
    );

    // ---- 4. long ROUTED main content scrolls in its GENUINE scroller -----
    // `<main>` is `flex-1 overflow-hidden`, so appending a synthetic 4000px
    // div to it only proves a hidden box hides things — it says nothing about
    // whether long content is REACHABLE. So the mock serves a genuinely long
    // REAL dashboard payload and this scenario measures the real routed page
    // in its real scroller (`ContentWrap`'s `h-full overflow-y-auto` div):
    // the last row must start off-screen, become visible after a POSITIVE
    // scroll of that scroller, and neither state may move the sidebar or the
    // document.
    await pw(['goto', `${srv.url}/__state?name=long-content`]);
    await openSession(srv.url, {
      width: 1280,
      height: 600,
      theme: 'light',
      route: `/orgs/${ORG}/dashboard`,
    });
    const longMain = await evaluate(`() => {
      const main = document.querySelector('main');
      const aside = document.querySelector('aside[role="navigation"]');
      const de = document.documentElement;
      const marker = ${JSON.stringify(LAST_ROW_MARKER)};
      const snap = () => ({
        asideHeight: +aside.getBoundingClientRect().height.toFixed(2),
        asideBottom: +aside.getBoundingClientRect().bottom.toFixed(2),
        documentScrollHeight: de.scrollHeight,
        documentClientHeight: de.clientHeight,
        windowScrollY: window.scrollY,
      });
      // The genuine scroller: the nearest overflowing scroll container inside
      // <main>. Never a harness-injected node.
      const scrollers = [main, ...main.querySelectorAll('*')].filter((el) => {
        const cs = getComputedStyle(el);
        return /auto|scroll/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 1;
      });
      const target = scrollers[0] || null;
      // Last REAL content row, found by its payload text — not by a test hook.
      const last = [...main.querySelectorAll('span')].find(
        (el) => el.textContent.trim() === marker,
      ) || null;
      const vis = (el, sc) => {
        if (!el) return null;
        const b = el.getBoundingClientRect();
        const s = sc ? sc.getBoundingClientRect() : null;
        return {
          rect: { top: +b.top.toFixed(2), bottom: +b.bottom.toFixed(2) },
          inViewport: b.top >= -0.5 && b.bottom <= innerHeight + 0.5,
          inScrollport: s ? b.top >= s.top - 0.5 && b.bottom <= s.bottom + 0.5 : null,
        };
      };
      const geomBefore = snap();
      const beforeScroll = {
        scrollerTag: target ? target.tagName.toLowerCase() : null,
        scrollerClass: target ? target.className : null,
        scrollerIsMain: target === main,
        scrollerScrollTop: target ? target.scrollTop : null,
        scrollerScrollHeight: target ? target.scrollHeight : null,
        scrollerClientHeight: target ? target.clientHeight : null,
        lastRowFound: !!last,
        lastRow: vis(last, target),
      };
      if (target) target.scrollTop = target.scrollHeight;
      const afterScroll = {
        scrollerScrollTop: target ? +target.scrollTop.toFixed(2) : null,
        scrolledToEnd: target
          ? target.scrollTop + target.clientHeight >= target.scrollHeight - 1
          : false,
        lastRow: vis(last, target),
      };
      const geomAfter = snap();
      if (target) target.scrollTop = 0;
      return JSON.stringify({
        source: 'real routed dashboard content (long-content shell state)',
        expectedRows: ${LONG_ACTIVITY_ROWS},
        renderedMarkerRows: [...main.querySelectorAll('span')].filter(
          (el) => el.textContent.trim() === marker,
        ).length,
        mainOverflowY: getComputedStyle(main).overflowY,
        viewportHeight: innerHeight,
        beforeScroll,
        afterScroll,
        geomBefore,
        geomAfter,
      });
    }`);
    ledger.push({ scenario: 'laptop-1280x600-light-long-main-content', longMain });
    shots.push({
      scenario: 'laptop-1280x600-light-long-main-content-scrolled',
      ...(await shot(join(OUT, 'sidebar-laptop-1280x600-light-long-content.png'))),
    });

    checks.check(
      'long-content-is-genuinely-long-and-initially-unreachable:1280x600',
      'The routed page really overflows its own scroller and its last row starts off-screen',
      longMain.renderedMarkerRows === 1 &&
        longMain.beforeScroll.lastRowFound === true &&
        longMain.beforeScroll.scrollerScrollHeight >
          longMain.beforeScroll.scrollerClientHeight + 1 &&
        longMain.beforeScroll.lastRow.inViewport === false,
      longMain.beforeScroll,
    );
    checks.check(
      'long-content-last-row-reachable-by-internal-scroll:1280x600',
      'Scrolling the genuine main scroller brings the LAST real row fully into view',
      longMain.afterScroll.scrollerScrollTop > 0 &&
        longMain.afterScroll.scrolledToEnd === true &&
        longMain.afterScroll.lastRow.inViewport === true &&
        longMain.afterScroll.lastRow.inScrollport === true,
      longMain.afterScroll,
    );
    checks.check(
      'long-main-content-does-not-expand-sidebar:1280x600',
      'Long main-page content does not expand the sidebar or the document, before or after scrolling',
      [longMain.geomBefore, longMain.geomAfter].every(
        (g) =>
          g.asideBottom <= longMain.viewportHeight + 0.5 &&
          g.asideHeight <= longMain.viewportHeight + 0.5 &&
          g.documentScrollHeight <= g.documentClientHeight + 0.5 &&
          g.windowScrollY === 0,
      ) &&
        longMain.geomAfter.asideHeight === longMain.geomBefore.asideHeight &&
        longMain.geomAfter.asideBottom === longMain.geomBefore.asideBottom,
      { geomBefore: longMain.geomBefore, geomAfter: longMain.geomAfter },
    );
    await pw(['goto', `${srv.url}/__state?name=populated`]);

    // ---- 4b. shell data states -------------------------------------------
    // The sidebar consumes exactly two reads: the org list and the dashboard
    // summary's org_age_days. These are the states that can move its layout.
    for (const state of ['populated', 'empty', 'loading', 'error', 'no-org']) {
      await pw(['goto', `${srv.url}/__state?name=${state}`]);
      const allowPageError = state === 'error' || state === 'loading';
      await openSession(srv.url, {
        width: 1280,
        height: 600,
        theme: 'light',
        route: `/orgs/${ORG}/dashboard`,
        allowPageError,
      });
      const g = await evaluate(GEOMETRY_PROBE);
      ledger.push({ scenario: `shell-state-${state}-1280x600-light`, geometry: g });
      shots.push({
        scenario: `shell-state-${state}-1280x600-light`,
        ...(await shot(join(OUT, `sidebar-state-${state}-1280x600-light.png`))),
      });
      checks.check(
        `shell-state-fits:${state}`,
        `Shell data state "${state}" keeps the sidebar inside the viewport with reachable footer controls`,
        g.aside.rect.bottom <= g.viewport.height + 0.5 &&
          g.document.scrollHeight <= g.document.clientHeight + 0.5 &&
          g.settings.visibility.inViewport &&
          g.account.visibility.inViewport &&
          g.navItemCount === EXPECTED_NAV_LINKS,
        {
          asideBottom: g.aside.rect.bottom,
          docScrollHeight: g.document.scrollHeight,
          docClientHeight: g.document.clientHeight,
          settingsInViewport: g.settings.visibility.inViewport,
          accountInViewport: g.account.visibility.inViewport,
          navItemCount: g.navItemCount,
        },
      );
    }
    await pw(['goto', `${srv.url}/__state?name=populated`]);
    await openSession(srv.url, { width: 1280, height: 600, theme: 'light', route: `/orgs/${ORG}/dashboard` });

    // ---- 5. resize cycles -------------------------------------------------
    const resizeLedger = [];
    for (const [w, h] of [
      [1280, 800],
      [1280, 600],
      [1280, 800],
      [768, 700],
      [767, 700],
      [768, 700],
    ]) {
      await pw(['resize', String(w), String(h)]);
      await sleep(500);
      const g = await evaluate(GEOMETRY_PROBE);
      resizeLedger.push({
        viewport: [w, h],
        asideBottom: g.aside.rect.bottom,
        asideWidth: g.aside.rect.width,
        docScrollHeight: g.document.scrollHeight,
        docClientHeight: g.document.clientHeight,
        navClientHeight: g.nav.clientHeight,
        navScrollHeight: g.nav.scrollHeight,
        settingsInViewport: g.settings.visibility.inViewport,
        accountInViewport: g.account.visibility.inViewport,
      });
    }
    ledger.push({ scenario: 'resize-cycle', resizeLedger });
    checks.check(
      'resize-cycle-stable',
      'Resize 800->600->800 and across the md boundary keeps the sidebar inside the viewport',
      resizeLedger.every(
        (r) =>
          r.asideBottom <= r.viewport[1] + 0.5 &&
          r.docScrollHeight <= r.docClientHeight + 0.5 &&
          r.settingsInViewport &&
          r.accountInViewport,
      ),
      resizeLedger,
    );
    checks.check(
      'md-boundary-rail-width',
      'The md boundary still flips the rail between 244px and 56px',
      resizeLedger.find((r) => r.viewport[0] === 768).asideWidth === 244 &&
        resizeLedger.find((r) => r.viewport[0] === 767).asideWidth === 56,
      resizeLedger.filter((r) => r.viewport[0] < 1280),
    );

    // ---- 6. adversarial red-side probes ----------------------------------
    // Both probes must FLIP the corresponding assertion to failing; if they do
    // not, the assertion is not actually measuring what it claims.
    await pw(['resize', '1280', '600']);
    await sleep(400);
    const displacement = await evaluate(`() => {
      const aside = document.querySelector('aside[role="navigation"]');
      aside.style.transform = 'translateY(120px)';
      const b = aside.getBoundingClientRect();
      const acc = document.querySelector('[aria-label^="Account:"]').getBoundingClientRect();
      const res = { asideBottom: +b.bottom.toFixed(2), accountBottom: +acc.bottom.toFixed(2), viewportHeight: innerHeight,
                    asideInsideViewport: b.bottom <= innerHeight + 0.5,
                    accountInsideViewport: acc.bottom <= innerHeight + 0.5 };
      aside.style.transform = '';
      return JSON.stringify(res);
    }`);
    checks.check(
      'red-side-displacement-detected',
      'Negative probe: a 120px downward displacement of the sidebar IS detected',
      displacement.asideInsideViewport === false && displacement.accountInsideViewport === false,
      displacement,
    );

    const ancestorClip = await evaluate(`() => {
      const nav = document.querySelector('nav[aria-label="Primary navigation items"]');
      const last = [...nav.querySelectorAll('a, span[aria-disabled="true"]')].pop();
      const before = last.getBoundingClientRect();
      // A wrapper SHORTER than the item itself, so the item is genuinely
      // clipped by the injected ancestor regardless of any scroll position.
      const wrapper = document.createElement('div');
      wrapper.id = 'thr230-red-side-clip';
      wrapper.style.cssText = 'overflow:hidden;height:10px';
      const parent = last.parentElement;
      parent.insertBefore(wrapper, last);
      wrapper.appendChild(last);
      const chain = (el) => { const out = []; let n = el.parentElement;
        while (n) { const cs = getComputedStyle(n);
          if (/hidden|scroll|auto|clip/.test(cs.overflowY)) { const b = n.getBoundingClientRect();
            out.push({ id: n.id || null, tag: n.tagName.toLowerCase(), overflowY: cs.overflowY,
                       top: +b.top.toFixed(2), bottom: +b.bottom.toFixed(2) }); }
          n = n.parentElement; } return out; };
      const b = last.getBoundingClientRect();
      let clippedBy = null;
      for (const a of chain(last)) { if (b.top < a.top - 0.5 || b.bottom > a.bottom + 0.5) { clippedBy = a; break; } }
      const res = {
        detected: !!clippedBy,
        clippedBy,
        clippedByInjectedWrapper: !!clippedBy && clippedBy.id === 'thr230-red-side-clip',
        itemHeight: +before.height.toFixed(2),
        wrapperHeight: 10,
      };
      parent.insertBefore(last, wrapper);
      wrapper.remove();
      return JSON.stringify(res);
    }`);
    checks.check(
      'red-side-ancestor-clip-detected',
      'Negative probe: an injected overflow:hidden ancestor IS detected as clipping',
      ancestorClip.detected === true && ancestorClip.clippedByInjectedWrapper === true,
      ancestorClip,
    );

    await pw(['close']).catch(() => {});
  } catch (err) {
    // An early scenario failure must NOT lose the diagnostics gathered so far:
    // record it and fall through so the full ledger is still written to disk.
    fatal = { message: String(err && err.message ? err.message : err), stack: err && err.stack ? String(err.stack) : null };
    console.error(`[thr230:${LABEL}] FATAL scenario error: ${fatal.message}`);
  } finally {
    await pw(['close']).catch((e) => cleanupErrors.push(`browser close: ${String(e && e.message ? e.message : e)}`));
    // Always persist the fail-closed route ledger, even when a scenario threw:
    // the violation list is exactly how the allowlist is discovered (MEM-172).
    // A failure to write it is a recorded cleanup error, never a silent pass.
    await writeFile(
      join(OUT, 'thr230-api-routes.json'),
      JSON.stringify(
        {
          allowlist: [...API_ALLOWLIST.keys()],
          query_contracts: [...API_QUERY_CONTRACTS.keys()],
          served: [...new Set(srv.served)],
          violations: srv.violations,
        },
        null,
        2,
      ),
    ).catch((e) => cleanupErrors.push(`api-routes write: ${String(e && e.message ? e.message : e)}`));
    try {
      await srv.close();
    } catch (e) {
      cleanupErrors.push(`server close: ${String(e && e.message ? e.message : e)}`);
    }
  }

  // ---- manifest ----------------------------------------------------------
  const stale = shots.filter((s) => statSync(s.file).mtimeMs < startedAt);
  const scriptSha = createHash('sha256')
    .update(await readFile(fileURLToPath(import.meta.url)))
    .digest('hex');
  const indexSha = createHash('sha256').update(await readFile(join(DIST, 'index.html'))).digest('hex');

  const manifest = {
    task: 'TASK-6955',
    provenance_chain: ['TASK-6924', 'TASK-6925', 'TASK-6933', 'TASK-6952', 'TASK-6955'],
    thread: 'THR-230',
    label: LABEL,
    run_nonce: RUN_NONCE,
    started_at: new Date(startedAt).toISOString(),
    finished_at: new Date().toISOString(),
    dist: DIST,
    dist_index_sha256: indexSha,
    script: 'web/scripts/screenshot-harness/shot-thr230-sidebar-viewport.mjs',
    script_sha256: scriptSha,
    api_allowlist: [...API_ALLOWLIST.keys()],
    api_query_contracts: [...API_QUERY_CONTRACTS.entries()].map(([k, c]) => ({
      key: k,
      allowed: c.allowed,
      required: c.required,
      value_contract: { group_by: 'agent|thread', since: 'ISO-8601 UTC instant' },
    })),
    api_served: [...new Set(srv.served)],
    api_violations: srv.violations,
    violation_count: srv.violations.length,
    browser_default_refusals: [...new Set(srv.browserDefaultRefusals)],
    browser_default_refusal_count: srv.browserDefaultRefusals.length,
    stale_artifacts: stale.map((s) => s.file),
    screenshots: shots,
    screenshot_note:
      'The app shell is h-full with an internal scroller; a screenshot equals the CSS ' +
      'viewport and does NOT reveal content below the fold (MEM-172/MEM-173). Off-screen ' +
      'and scrolled states are proven by the geometry ledger and the interaction probes, ' +
      'not by the PNGs alone.',
    fatal_error: fatal,
    cleanup_errors: cleanupErrors,
    checks: checks.results,
    passed: checks.results.filter((r) => r.pass).length,
    failed: checks.failed.length,
    failed_ids: checks.failed.map((f) => f.id),
    ledger,
  };

  await writeFile(join(OUT, 'thr230-ledger.json'), JSON.stringify(manifest, null, 2));

  // Infrastructure health is judged SEPARATELY from the checks, in both
  // labels: a run whose mock, browser, or artifacts misbehaved has produced no
  // usable evidence at all, and a `before` run in that state must NEVER be
  // read as "the bug reproduced".
  const infraHealthy =
    fatal === null &&
    cleanupErrors.length === 0 &&
    srv.violations.length === 0 &&
    stale.length === 0 &&
    srv.browserDefaultRefusals.length === 0;
  const clean = infraHealthy && checks.failed.length === 0;

  console.log(
    `[thr230:${LABEL}] checks ${manifest.passed} passed / ${manifest.failed} failed; ` +
      `api violations ${srv.violations.length}; stale ${stale.length}; ` +
      `cleanup errors ${cleanupErrors.length}; fatal ${fatal ? 'YES' : 'no'}`,
  );
  for (const f of checks.failed) console.log(`  FAIL ${f.id} — ${f.criterion}`);
  console.log(`[thr230:${LABEL}] ledger -> ${join(OUT, 'thr230-ledger.json')}`);

  if (LABEL === 'before') {
    // A valid baseline is not "anything that failed". It must be the SPECIFIC
    // defect under repair — the rail overflowing the short laptop viewport and
    // taking the footer out of reach at 1280x600 — observed on an otherwise
    // healthy run. Every other outcome (a green baseline, an infrastructure
    // failure, or a failure in the self-test/design-preservation checks) means
    // the run proves nothing and is reported as such.
    const failedIds = new Set(checks.failed.map((f) => f.id));
    const REQUIRED_BASELINE_FAILURES = [
      'aside-inside-viewport:laptop-1280x600/light',
      'no-document-scroll:laptop-1280x600/light',
      'footer-settings-reachable:laptop-1280x600/light',
      'footer-account-reachable:laptop-1280x600/light',
    ];
    // These must PASS even on the baseline: the two adversarial self-tests
    // prove the probes can still see a defect, and the width/inventory checks
    // prove the build under test is the real app, not a broken render.
    const MUST_PASS_ON_BASELINE = checks.results
      .map((r) => r.id)
      .filter(
        (id) =>
          id.startsWith('red-side-') ||
          id.startsWith('nav-item-count:') ||
          id.startsWith('rail-width:'),
      );

    const missing = REQUIRED_BASELINE_FAILURES.filter((id) => !failedIds.has(id));
    const wronglyFailed = MUST_PASS_ON_BASELINE.filter((id) => failedIds.has(id));
    const reproduced =
      infraHealthy && missing.length === 0 && wronglyFailed.length === 0;

    manifest.baseline_verdict = {
      reproduced,
      infra_healthy: infraHealthy,
      required_failures: REQUIRED_BASELINE_FAILURES,
      missing_required_failures: missing,
      self_test_or_preservation_checks_that_wrongly_failed: wronglyFailed,
      fatal_error: fatal,
      cleanup_errors: cleanupErrors,
      api_violations: srv.violations.length,
      stale_artifacts: stale.length,
      browser_default_refusals: srv.browserDefaultRefusals.length,
    };
    await writeFile(join(OUT, 'thr230-ledger.json'), JSON.stringify(manifest, null, 2));

    if (!reproduced) {
      console.error(
        '[thr230:before] ERROR: this run is NOT a valid reproduction of the defect — ' +
          JSON.stringify(manifest.baseline_verdict),
      );
      process.exit(1);
    }
    console.log(
      `[thr230:before] defect reproduced on a healthy run: ${REQUIRED_BASELINE_FAILURES.join(', ')}`,
    );
    process.exit(0);
  }
  process.exit(clean ? 0 : 1);
}

run().catch((e) => {
  console.error(e);
  process.exit(2);
});
