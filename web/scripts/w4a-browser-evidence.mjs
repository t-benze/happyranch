#!/usr/bin/env node
/**
 * W4a browser-evidence harness (THR-118 W4a-1 health + dreams; W4a-2/W4a-3 add rows).
 *
 * Same mechanism as `w3b-jobs-browser-evidence.mjs` (no new dependency): ONE
 * isolated headless Chrome driven over the DevTools Protocol against the
 * ORDINARY production SPA bundle (no build flag), served same-origin next to a
 * synthetic `/api/v1` stub whose every request lands in a server-side ledger.
 * Expected copy is read from the shipped typed catalogs.
 *
 * Everything route-specific lives in three tables so a later slice only adds
 * rows: `API_ROUTES` (synthetic daemon payloads), `VIEW_ROUTES` (case V) and
 * `SWITCH_ROUTES` (case S). Cases (receipt.json; exit 1 if any fails):
 *   G  the ordinary bundle carries every VIEW_ROUTES row's zh-CN catalog copy;
 *   V  each VIEW_ROUTES row in en and zh-CN at 1440x900 and 390x844:
 *      <html lang>, localized chrome, daemon values verbatim, no document-level
 *      horizontal overflow, PNG + sha256;
 *   S  each SWITCH_ROUTES row (an input/dialog surface): focus the control,
 *      switch en -> zh-CN -> en via the storage-event path; the SAME container
 *      and control nodes, focus (and draft, when the row types one), localized
 *      copy, ZERO /api requests per switch window.
 *
 * Build + run (from web/):
 *   ./node_modules/.bin/vite build --outDir <tmp>/dist-ordinary
 *   node scripts/w4a-browser-evidence.mjs --dist <tmp>/dist-ordinary \
 *     --out <evidence dir> --head <sha>
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const { en } = await import(join(HERE, '../src/lib/i18n/locales/en.ts'));
const { zhCN } = await import(join(HERE, '../src/lib/i18n/locales/zh-CN.ts'));
const CATALOG = { en, 'zh-CN': zhCN };
/** Catalog lookup with named params and explicit plurals (mirrors catalog.ts). */
function tr(locale, key, params = {}) {
  let value = CATALOG[locale][key];
  if (value === undefined) throw new Error(`harness: unknown catalog key ${key}`);
  if (typeof value !== 'string') {
    const n = Number(params.count);
    value = locale === 'en' && n === 1 && value.one !== undefined ? value.one : value.other;
  }
  return value.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 || index + 1 >= process.argv.length ? fallback : process.argv[index + 1];
}

const dist = arg('dist') && resolve(arg('dist'));
const outDir = resolve(arg('out', '.w4a-evidence'));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

const ORG = 'test-org';
const LOCALE_KEY = 'happyranch.ui.locale';
const EXTERNAL_BLOCK = ['https://*', 'http://*.com/*', 'http://*.net/*'];
const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.ico': 'image/x-icon',
};

function distJs(dir) {
  const out = [];
  const walk = (d) => {
    for (const entry of readdirSync(d, { withFileTypes: true })) {
      const p = join(d, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (p.endsWith('.js')) out.push(p);
    }
  };
  walk(dir);
  return out;
}

// ------------------------------------------------------------------ CDP driver
class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    this.ready = new Promise((ok, fail) => {
      this.ws.addEventListener('open', () => ok());
      this.ws.addEventListener('error', () => fail(new Error('CDP websocket error')));
    });
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(typeof event.data === 'string' ? event.data : Buffer.from(event.data).toString('utf8'));
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
        return;
      }
      for (const handler of [...(this.handlers.get(message.method) || [])]) handler(message);
    });
  }

  async send(method, params = {}, sessionId) {
    await this.ready;
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    const promise = new Promise((ok, fail) => this.pending.set(id, { resolve: ok, reject: fail }));
    this.ws.send(JSON.stringify(payload));
    return promise;
  }

  waitFor(method, { sessionId, timeout = 30000 } = {}) {
    return new Promise((ok, fail) => {
      const handler = (message) => {
        if (sessionId && message.sessionId !== sessionId) return;
        off();
        clearTimeout(timer);
        ok(message.params);
      };
      this.handlers.set(method, [...(this.handlers.get(method) || []), handler]);
      const off = () => this.handlers.set(method, (this.handlers.get(method) || []).filter((h) => h !== handler));
      const timer = setTimeout(() => { off(); fail(new Error(`timeout waiting for CDP ${method}`)); }, timeout);
    });
  }

  close() {
    try { this.ws.close(); } catch { /* closed */ }
  }
}

async function waitForDevTools(userDataDir, timeout = 30000) {
  const file = join(userDataDir, 'DevToolsActivePort');
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (existsSync(file)) {
      const [port, path] = readFileSync(file, 'utf8').split('\n');
      if (port && Number(port) > 0 && path) return { port: Number(port), path: path.trim() };
    }
    await sleep(100);
  }
  throw new Error(`Chrome DevTools port file not found at ${file}`);
}


// ------------------------------------------------------------------ synthetic API
const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const LEDGER = [];
const HUNG = [];

// Daemon bytes that must survive every locale unchanged.
const HEALTH_SNAPSHOT = {
  uptime_seconds: 3 * 3600 + 14 * 60,
  loops: { work_hours_scheduler_loop: { last_tick_iso: iso(30e3), interval_seconds: 60, last_duration_seconds: 0.012 } },
  http: {
    __all__: { count: 120, p50: 0.01, p95: 0.05, max: 0.2 },
    'GET /api/v1/orgs/{slug}/tasks': { count: 42, p50: 0.008, p95: 0.02, max: 0.05 },
  },
  tasks: { pending_and_in_flight: 4 }, jobs_in_flight: 2, executor_sessions_active: 1, run_step_queue_depth: 7,
};
const DREAM_BASE = {
  scheduled_for: iso(9 * 3600e3), window_start: null, window_end: iso(8 * 3600e3), started_at: iso(9 * 3600e3),
  ended_at: null, transcript_path: null, founder_thread_id: null, error: null, summary: null,
};
const DREAMS = [
  { ...DREAM_BASE, dream_id: 'DREAM-0012', agent_name: 'engineering_manager', local_date: '2026-09-30', status: 'completed',
    summary: 'Routine nightly reflection — «raw» summary.', new_learnings_count: 2, kb_candidate_count: 0 },
  { ...DREAM_BASE, dream_id: 'DREAM-0011', agent_name: 'product_lead', local_date: '2026-09-30', status: 'completed',
    summary: 'Identified a recurring pattern.', new_learnings_count: 1, kb_candidate_count: 1, founder_thread_id: 'THR-010' },
  { ...DREAM_BASE, dream_id: 'DREAM-0009', agent_name: 'dev_agent', local_date: '2026-09-29', status: 'failed',
    new_learnings_count: 0, kb_candidate_count: 0, error: 'Executor API returned 503' },
];
const DREAM_DETAIL = {
  ...DREAMS[1], transcript: '## Reflection\n\nIdentified a recurring pattern.',
  kb_candidates: [{
    id: 7, dream_id: 'DREAM-0011', agent_name: 'product_lead', slug: 'spanish-after-hours', title: 'Spanish after-hours routing',
    topic: 'support', rationale: 'Seen three times this week.', body_markdown: 'Route Spanish tickets after 18:00.',
    status: 'pending', promoted_kb_slug: null, created_at: iso(8 * 3600e3), updated_at: iso(8 * 3600e3),
  }],
};

/** pathname -> payload (or (search) => payload). W4a-2/W4a-3 add rows here. */
const API_ROUTES = {
  '/api/v1/auth/bootstrap': { token: 'w4a-evidence-token' },
  '/api/v1/orgs': { orgs: [{ slug: ORG, root: `/runtime/${ORG}` }], broken: [] },
  [`/api/v1/orgs/${ORG}/dashboard/summary`]: { org_age_days: 12 },
  // W4a-1 health
  '/api/v1/metrics': HEALTH_SNAPSHOT,
  '/api/v1/metrics/history': {
    snapshots: [1, 2, 3].map((id) => ({ id, captured_at: iso(id * 60e3), snapshot_json: JSON.stringify({ ...HEALTH_SNAPSHOT, run_step_queue_depth: id }) })),
  },
  // W4a-1 dreams
  [`/api/v1/orgs/${ORG}/dreams`]: { dreams: DREAMS },
  [`/api/v1/orgs/${ORG}/dreams/DREAM-0011`]: DREAM_DETAIL,
};

function api(pathname, search) {
  const row = API_ROUTES[pathname];
  if (row === undefined) return {};
  return typeof row === 'function' ? row(search) : row;
}

function startServer(root) {
  return new Promise((ok, fail) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        const p = url.pathname;
        if (p.startsWith('/api/')) {
          LEDGER.push({ method: request.method, path: p, search: url.search, t: Date.now() });
          if (p.endsWith('/events') || p.includes('/stream') || p.endsWith('/tail')) {
            response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
            HUNG.push(response);
            return;
          }
          response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
          response.end(JSON.stringify(api(p, url.search)));
          return;
        }
        if (p === '/__w4a_blank') {
          response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
          response.end('<!doctype html><title>w4a second tab</title>');
          return;
        }
        let file = p === '/' ? join(root, 'index.html') : join(root, decodeURIComponent(p));
        if (!existsSync(file) || statSync(file).isDirectory()) file = join(root, 'index.html');
        response.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
        response.end(readFileSync(file));
      } catch (error) {
        response.writeHead(500);
        response.end(String(error));
      }
    });
    server.on('error', fail);
    server.listen(0, '127.0.0.1', () => ok({ server, url: `http://127.0.0.1:${server.address().port}` }));
  });
}

const seedLocale = (locale) => `try { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); } catch (e) {}`;
const CHINESE_NAVIGATOR = `(() => {
  Object.defineProperty(Navigator.prototype, 'language', { get: () => 'zh-CN', configurable: true });
  Object.defineProperty(Navigator.prototype, 'languages', { get: () => ['zh-CN', 'zh'], configurable: true });
})();`;
// textContent, not innerText: CSS text-transform must not hide catalog bytes.
const bodyHas = (s) => `document.body.textContent.includes(${JSON.stringify(s)})`;
const langIs = (l) => `document.documentElement.lang === ${JSON.stringify(l)}`;
const noOverflow = `document.documentElement.scrollWidth <= innerWidth + 1`;

// ------------------------------------------------------------------ route tables
const DREAM_CARD = (id) => `[...document.querySelectorAll('li > button')].find((b) => b.textContent.includes(${JSON.stringify(id)}))`;

/**
 * Case V rows. `keys` are catalog keys (string or [key, params]) that must be
 * visible; `verbatim` are daemon bytes that must render unchanged; `ready` is
 * the content predicate; optional `prep(page, h)` drives the page to the state.
 */
const VIEW_ROUTES = [
  {
    id: 'health', route: 'health', path: `/orgs/${ORG}/health`,
    ready: () => `${bodyHas('work_hours_scheduler_loop')} && ${bodyHas('GET /api/v1/orgs/{slug}/tasks')}`,
    keys: [
      'health.title', 'health.meta', 'health.metric.uptime', 'health.metric.uptimeHint', 'health.metric.queue',
      'health.loops.title', 'health.loops.column.lastDuration', 'health.http.title', 'health.http.allRoutes',
      'health.metric.p95', ['health.trends.title', { window: '@health.window.24h' }], ['health.trends.snapshots', { count: 3 }],
      ['health.uptime.hours', { hours: 3, minutes: 14 }], ['health.loops.interval', { value: 60 }],
    ],
    verbatim: ['work_hours_scheduler_loop', 'GET /api/v1/orgs/{slug}/tasks'],
  },
  {
    id: 'dreams', route: 'dreams', path: `/orgs/${ORG}/dreams`,
    ready: () => `${bodyHas('DREAM-0012')} && ${bodyHas('DREAM-0009')}`,
    keys: [
      ['dreams.header.eyebrow', { count: 2 }], 'dreams.header.statement', ['dreams.feed.count', { count: 3 }],
      'dreams.status.completed', 'dreams.status.failed', 'dreams.quiet.title', 'dreams.thread.open', 'dreams.thread.none',
      'dreams.rail.overview', 'dreams.rail.candidates', 'dreams.rail.scheduleNote', ['dreams.count.reflections', { count: 3 }],
    ],
    verbatim: ['DREAM-0012', 'engineering_manager', 'Routine nightly reflection — «raw» summary.', 'Executor API returned 503', '2026-09-30'],
  },
  {
    id: 'dream-drawer', route: 'dreams (detail drawer)', path: `/orgs/${ORG}/dreams`,
    ready: () => `${bodyHas('DREAM-0011')}`,
    prep: async (page, h) => {
      await h.clickSrc(page, DREAM_CARD('DREAM-0011'));
      await h.waitTrue(page, bodyHas('Spanish after-hours routing'), 'drawer content');
    },
    keys: [
      ['dreams.candidate.pending', { agent: 'product_lead' }], 'dreams.candidate.accept', 'dreams.candidate.dismiss',
      ['dreams.detail.toReview', { count: 1 }], 'dreams.rail.candidates',
    ],
    verbatim: ['product_lead · 2026-09-30', 'Spanish after-hours routing', 'spanish-after-hours', 'Seen three times this week.'],
  },
];

/**
 * Case S rows (surfaces with an input or dialog). `open` drives to the
 * surface; `container`/`control` are DOM expressions; `copy(locale)` lists
 * catalog strings that must be visible after each switch; `draft` (optional)
 * is typed into the control first.
 */
const SWITCH_ROUTES = [
  {
    id: 'dream-drawer-accept', path: `/orgs/${ORG}/dreams`,
    open: async (page, h) => {
      await h.waitTrue(page, bodyHas('DREAM-0011'), 'feed');
      await h.clickSrc(page, DREAM_CARD('DREAM-0011'));
      await h.waitTrue(page, bodyHas('Spanish after-hours routing'), 'drawer content');
    },
    control: `[...document.querySelectorAll('[role="dialog"] button')].find((b) => b.getAttribute('aria-label') === ${JSON.stringify(tr('en', 'dreams.candidate.accept'))} || b.getAttribute('aria-label') === ${JSON.stringify(tr('zh-CN', 'dreams.candidate.accept'))})`,
    container: `(CONTROL || { closest: () => null }).closest('[role="dialog"]')`,
    copy: (locale) => [tr(locale, 'dreams.candidate.pending', { agent: 'product_lead' }), tr(locale, 'dreams.candidate.accept'), tr(locale, 'dreams.rail.candidates')],
    shot: 'zh-dream-drawer-switch-1440',
  },
];

/** Resolve a VIEW_ROUTES key spec; a param value '@key' is itself translated. */
function expected(locale, spec) {
  const [key, params = {}] = Array.isArray(spec) ? spec : [spec];
  const resolved = Object.fromEntries(Object.entries(params).map(([k, v]) => [k, typeof v === 'string' && v.startsWith('@') ? tr(locale, v.slice(1)) : v]));
  return { key, text: tr(locale, key, resolved) };
}

async function main() {
  if (!dist || !existsSync(join(dist, 'index.html'))) throw new Error('missing --dist <dir> (ordinary build)');
  mkdirSync(outDir, { recursive: true });
  const cases = [];
  const screenshots = [];
  let current = null;
  const beginCase = (id, title) => { current = { id, title, checks: [], pass: false }; cases.push(current); console.log(`\n=== ${id}: ${title}`); };
  const endCase = () => { current.pass = current.checks.length > 0 && current.checks.every((c) => c.ok); console.log(`--- ${current.id} ${current.pass ? 'PASS' : 'FAIL'}`); };
  function check(name, actual, expectedValue) {
    const ok = JSON.stringify(actual) === JSON.stringify(expectedValue);
    current.checks.push({ name, actual, expected: expectedValue, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)} expected=${JSON.stringify(expectedValue)}`}`);
  }

  const joined = distJs(dist).map((f) => readFileSync(f, 'utf8')).join('\n');
  const bundleKeys = [...new Set(VIEW_ROUTES.flatMap((row) => row.keys.map((spec) => (Array.isArray(spec) ? spec[0] : spec))))];
  // Template text before the first placeholder is what a minifier keeps verbatim.
  const literal = (key) => { const v = CATALOG['zh-CN'][key]; return (typeof v === 'string' ? v : v.other).split('{')[0].trim(); };
  const fingerprint = {
    indexHtmlSha256: sha256(readFileSync(join(dist, 'index.html'))),
    zhCatalogLiterals: Object.fromEntries(bundleKeys.map((key) => [key, literal(key) === '' || joined.includes(JSON.stringify(literal(key)).slice(1, -1)) || joined.includes(literal(key))])),
  };
  const { server, url: base } = await startServer(dist);
  const userDataDir = `/tmp/w4a-${process.pid}`; // short: Chrome's singleton socket path limit
  rmSync(userDataDir, { recursive: true, force: true });
  mkdirSync(userDataDir, { recursive: true });
  let chrome;
  let cdp;
  let chromeVersion = null;
  try {
    chrome = spawn(chromeBin, [
      '--headless=new', '--lang=zh-CN', '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`,
      '--no-sandbox', '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--disable-dev-shm-usage',
      '--disable-extensions', '--disable-background-networking', '--hide-scrollbars', '--disable-crash-reporter',
      '--disable-background-timer-throttling', '--disable-renderer-backgrounding', 'about:blank',
    ], { stdio: ['ignore', 'ignore', 'ignore'], env: { ...process.env, HOME: userDataDir, TMPDIR: userDataDir } });
    const devtools = await waitForDevTools(userDataDir);
    chromeVersion = (await (await fetch(`http://127.0.0.1:${devtools.port}/json/version`)).json()).Browser;
    cdp = new CDP(`ws://127.0.0.1:${devtools.port}${devtools.path}`);
    await cdp.ready;

    async function openPage(url, { init = '', width = 1440, height = 900 } = {}) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      for (const domain of ['Page', 'Runtime', 'Network']) await cdp.send(`${domain}.enable`, {}, sessionId);
      // Offline isolation: every non-loopback request (the external webfont
      // CSS/woff2) is refused, so a hung third-party fetch can never stall the
      // load event. Fonts fall back to local faces; recorded in the receipt.
      await cdp.send('Network.setBlockedURLs', { urls: EXTERNAL_BLOCK }, sessionId);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
      if (init) await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: init }, sessionId);
      const loaded = cdp.waitFor('Page.loadEventFired', { sessionId });
      await cdp.send('Page.navigate', { url }, sessionId);
      await loaded;
      return { targetId, sessionId };
    }
    const closePage = async (page) => { try { await cdp.send('Target.closeTarget', { targetId: page.targetId }); } catch { /* closed */ } };
    async function evaluate(page, expression) {
      const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, page.sessionId);
      if (result.exceptionDetails) throw new Error(`evaluate failed: ${result.exceptionDetails.exception?.description || result.exceptionDetails.text}`);
      return result.result.value;
    }
    async function waitTrue(page, expression, label, timeout = 15000) {
      const deadline = Date.now() + timeout;
      while (Date.now() < deadline) {
        if ((await evaluate(page, expression).catch(() => false)) === true) return true;
        await sleep(100);
      }
      console.log(`  (timeout waiting for ${label})`);
      return false;
    }
    async function clickSrc(page, src) {
      const box = await evaluate(page, `(() => { const el = (${src}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      if (!box) throw new Error(`clickSrc: not found ${src.slice(0, 120)}`);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
        await cdp.send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
      }
    }
    async function capture(page, name, meta) {
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await sleep(250);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
      const buffer = Buffer.from(data, 'base64');
      writeFileSync(join(outDir, `${name}.png`), buffer);
      screenshots.push({ name, file: `${name}.png`, sha256: sha256(buffer), bytes: buffer.length, ...meta });
    }
    /** Second same-origin tab writes the preference -> `storage` event in `page`. */
    async function crossTabSwitch(page, locale) {
      const other = await openPage(`${base}/__w4a_blank`);
      await evaluate(other, `(() => { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); return true; })()`);
      await closePage(other);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await waitTrue(page, langIs(locale), `storage-event switch to ${locale}`, 5000);
      await sleep(600); // quiescence: any switch-caused request lands in the ledger
    }
    const h = { clickSrc, waitTrue, evaluate };

    // ============================================================ G
    beginCase('G', 'ordinary bundle (no build flag) carries the zh-CN catalog copy of every V route');
    for (const [key, present] of Object.entries(fingerprint.zhCatalogLiterals)) check(`G ordinary JS contains zh-CN ${key}`, present, true);
    endCase();

    // ============================================================ V
    beginCase('V', `${VIEW_ROUTES.map((r) => r.id).join(' + ')}, en/zh-CN, 1440x900 + 390x844`);
    for (const locale of ['en', 'zh-CN']) {
      const short = locale === 'en' ? 'en' : 'zh';
      for (const [w, ht] of [[1440, 900], [390, 844]]) {
        for (const row of VIEW_ROUTES) {
          const page = await openPage(`${base}${row.path}`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}`, width: w, height: ht });
          await waitTrue(page, row.ready(locale), `${row.id} content`);
          if (row.prep) await row.prep(page, h);
          await sleep(400);
          check(`V ${row.id} ${locale} ${w} <html lang>`, await evaluate(page, langIs(locale)), true);
          for (const spec of row.keys) {
            const { key, text } = expected(locale, spec);
            check(`V ${row.id} ${locale} ${w} shows ${key}`, await evaluate(page, bodyHas(text)), true);
          }
          for (const value of row.verbatim) check(`V ${row.id} ${locale} ${w} verbatim ${value}`, await evaluate(page, bodyHas(value)), true);
          check(`V ${row.id} ${locale} ${w} no document horizontal overflow`, await evaluate(page, noOverflow), true);
          await capture(page, `${short}-${row.id}-${w}`, { viewport: `${w}x${ht}`, locale, route: row.route });
          await closePage(page);
        }
      }
    }
    endCase();

    // ============================================================ S
    beginCase('S', `${SWITCH_ROUTES.map((r) => r.id).join(' + ')}: en -> zh-CN -> en keeps nodes, focus (and draft), zero /api requests`);
    for (const row of SWITCH_ROUTES) {
      const page = await openPage(`${base}${row.path}`, { init: `${seedLocale('en')}\n${CHINESE_NAVIGATOR}` });
      await row.open(page, h);
      await sleep(400);
      const CONTROL = `(${row.control})`;
      const CONTAINER = row.container.replace('CONTROL', CONTROL);
      await waitTrue(page, `Boolean(${CONTROL})`, `${row.id} control`);
      await evaluate(page, `(() => { ${CONTROL}.focus(); return true; })()`);
      if (row.draft) { await cdp.send('Input.insertText', { text: row.draft }, page.sessionId); await sleep(200); }
      await evaluate(page, `(() => { window.__w4aBox = new WeakRef(${CONTAINER}); window.__w4aCtl = new WeakRef(${CONTROL}); return true; })()`);
      const state = `(() => {
        const box = window.__w4aBox.deref(); const ctl = window.__w4aCtl.deref();
        return {
          sameContainer: Boolean(box && box === ${CONTAINER} && box.isConnected && box.contains(ctl)),
          sameControl: Boolean(ctl && ctl === ${CONTROL} && ctl.isConnected),
          value: ctl && 'value' in ctl && ctl.tagName !== 'BUTTON' ? ctl.value : null,
          focused: document.activeElement === ctl, lang: document.documentElement.lang,
        };
      })()`;
      check(`S ${row.id} before switch: focus`, await evaluate(page, `document.activeElement === window.__w4aCtl.deref()`), true);
      for (const locale of ['zh-CN', 'en']) {
        const from = LEDGER.length;
        await crossTabSwitch(page, locale);
        const s = await evaluate(page, state);
        check(`S ${row.id} -> ${locale} same container node`, s.sameContainer, true);
        check(`S ${row.id} -> ${locale} same control node`, s.sameControl, true);
        if (row.draft) check(`S ${row.id} -> ${locale} draft value kept`, s.value, row.draft);
        check(`S ${row.id} -> ${locale} focus kept`, s.focused, true);
        for (const text of row.copy(locale)) check(`S ${row.id} -> ${locale} shows "${text}"`, await evaluate(page, bodyHas(text)), true);
        check(`S ${row.id} -> ${locale} <html lang>`, s.lang, locale);
        check(`S ${row.id} -> ${locale} zero /api requests in switch window`, LEDGER.slice(from), []);
        if (locale === 'zh-CN' && row.shot) await capture(page, row.shot, { viewport: '1440x900', locale, state: `${row.id} after en->zh-CN switch` });
      }
      await closePage(page);
    }
    endCase();
  } finally {
    for (const response of HUNG) { try { response.destroy(); } catch { /* gone */ } }
    if (cdp) cdp.close();
    if (chrome && !chrome.killed) chrome.kill('SIGKILL');
    server.closeAllConnections?.();
    server.close();
    rmSync(userDataDir, { recursive: true, force: true });
  }

  const failed = cases.filter((c) => !c.pass);
  const receipt = {
    head, generatedAt: new Date().toISOString(), node: process.version,
    chrome: { path: chromeBin, version: chromeVersion, lang: 'zh-CN (Chrome --lang + navigator override: environment never defaults the locale)' },
    externalRequestsBlocked: EXTERNAL_BLOCK,
    dist: fingerprint,
    routes: { view: VIEW_ROUTES.map((r) => r.id), switch: SWITCH_ROUTES.map((r) => r.id), api: Object.keys(API_ROUTES) },
    summary: cases.map((c) => ({ id: c.id, title: c.title, pass: c.pass, checks: c.checks.length, failedChecks: c.checks.filter((x) => !x.ok).map((x) => x.name) })),
    cases, screenshots, ledger: LEDGER, passed: cases.length - failed.length, failed: failed.length,
  };
  writeFileSync(join(outDir, 'receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`);
  writeFileSync(join(outDir, 'SHA256SUMS'), screenshots.map((s) => `${s.sha256}  ${s.file}`).join('\n') + '\n');
  console.log(`\n${receipt.passed}/${cases.length} cases passed; ${screenshots.length} screenshots in ${outDir}`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
