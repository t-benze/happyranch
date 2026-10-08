#!/usr/bin/env node
/**
 * W3b-2 Jobs + current full-mode Preferences browser-evidence harness (THR-118).
 *
 * Same mechanism as `w3b-tasks-browser-evidence.mjs` (no new dependency): ONE
 * isolated headless Chrome driven over the DevTools Protocol against the
 * ORDINARY production SPA bundle (no build flag), served same-origin next to a
 * synthetic `/api/v1` stub whose every request lands in a server-side ledger.
 * Chrome runs with `--lang=zh-CN` plus a Chinese `navigator.languages`
 * override, so an unset preference must follow the Chinese environment
 * in full mode. Historical W3b-2 receipts retain their preview identity. Expected copy is read from the shipped typed catalogs.
 *
 * Cases (receipt.json; exit 1 if any fails):
 *   G  the ordinary bundle now CONTAINS the Preferences selector markers;
 *   V  Jobs list + job detail in en and zh-CN at 1440x900 and 390x844:
 *      <html lang>, localized chrome, daemon values verbatim, no document-level
 *      horizontal overflow, PNG + sha256;
 *   S  Run-job dialog: type a cwd-override draft, switch en -> zh-CN -> en via
 *      the storage-event path; the SAME dialog and input nodes, the same value
 *      and focus, localized title/label, ZERO /api requests per switch window;
 *   P  Settings > Preferences in the ordinary build, preference UNSET on a
 *      Chinese browser: Chinese, Chinese radio checked, bilingual disclosure visible;
 *      real CDP clicks on English then 简体中文 switch in place (same radio/panel nodes,
 *      focus, zh disclosure, <html lang>) with ZERO /api requests.
 *
 * Build + run (from web/):
 *   ./node_modules/.bin/vite build --outDir <tmp>/dist-ordinary
 *   node scripts/w3b-jobs-browser-evidence.mjs --dist <tmp>/dist-ordinary \
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
const outDir = resolve(arg('out', '.w3b-jobs-evidence'));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');
const focusedCascade = arg('slice', 'all') === 'cascade';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

const ORG = 'test-org';
const LOCALE_KEY = 'happyranch.ui.locale';
const PREFERENCE_MARKERS = ['settings-preferences', 'happyranch-ui-language'];
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
// Daemon bytes that must survive every locale unchanged.
const AUTH = {
  title: 'Rotate staging TLS certs (authored job title)',
  script: "certbot renew --cert-name staging.example --deploy-hook 'systemctl reload nginx'",
  rationale: 'Certs expire in 3 days — «raw» rationale.',
};
const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const LEDGER = [];
const HUNG = [];
const CASCADE_TASK = { task_id: 'TASK-CASE-J', brief: 'Raw cascade task / 原文', status: 'in_progress', block_kind: 'blocked_on_job' };

function job(id, extra = {}) {
  return {
    id, task_id: 'TASK-640', agent_name: 'devops_agent', title: AUTH.title, rationale: AUTH.rationale,
    script_text: AUTH.script, interpreter: 'bash', cwd_hint: 'repos/infra', status: 'pending', exit_code: null,
    stdout_head: null, stderr_head: null, stdout_path: null, stderr_path: null, duration_ms: null,
    started_at: null, finished_at: null, reviewed_at: null, reviewed_by: null, reject_reason: null,
    cwd_resolved: null, max_runtime_seconds: 600, max_output_bytes: 52428800, review_required: true,
    persistent: false, reason: null, created_at: iso(14 * 60e3), ...extra,
  };
}
const JOBS = [
  job('JOB-901'),
  job('JOB-902', { status: 'running', title: 'Nightly backup', script_text: 'restic backup /srv', started_at: iso(5 * 60e3) }),
  job('JOB-903', { status: 'completed', exit_code: 0, review_required: false, title: 'Lint sweep', script_text: 'npm run lint' }),
  job('JOB-904', { status: 'rejected', title: 'Drop table', script_text: "psql -c 'DROP TABLE guides;'" }),
];

function api(pathname, search) {
  if (pathname === '/api/v1/auth/bootstrap') return { token: 'w3b2-evidence-token' };
  if (pathname === '/api/v1/orgs') return { orgs: [{ slug: ORG, root: `/runtime/${ORG}` }], broken: [] };
  if (pathname === `/api/v1/orgs/${ORG}/dashboard/summary`) return { org_age_days: 12 };
  if (pathname === `/api/v1/orgs/${ORG}/jobs/` || pathname === `/api/v1/orgs/${ORG}/jobs`) {
    const status = new URLSearchParams(search).get('status');
    return { jobs: status && status !== 'all' ? JOBS.filter((j) => j.status === status) : JOBS, next_cursor: null };
  }
  if (pathname === `/api/v1/orgs/${ORG}/jobs/JOB-901`) return JOBS[0];
  if (pathname === `/api/v1/orgs/${ORG}/tasks`) return { tasks: [], next_cursor: null };
  return {};
}

function startServer(root) {
  return new Promise((ok, fail) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        const p = url.pathname;
        if (p.startsWith('/api/')) {
          LEDGER.push({ method: request.method, path: p, search: url.search, t: Date.now() });
          if (focusedCascade && p === `/api/v1/orgs/${ORG}/tasks`) {
            const ref = new URL(request.headers.referer || 'http://127.0.0.1');
            const state = ref.searchParams.get('cascadeFixture') || 'populated';
            if (state === 'loading') { HUNG.push(response); return; }
            response.writeHead(state === 'error' ? 500 : 200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
            response.end(JSON.stringify(state === 'error' ? { detail: 'Raw diagnostic' } : { tasks: state === 'empty' ? [] : [CASCADE_TASK], next_cursor: null })); return;
          }
          if (p.endsWith('/events') || p.includes('/stream') || p.endsWith('/tail')) {
            response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
            HUNG.push(response);
            return;
          }
          response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
          response.end(JSON.stringify(api(p, url.search)));
          return;
        }
        if (p === '/__w3b_blank') {
          response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
          response.end('<!doctype html><title>w3b second tab</title>');
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

async function main() {
  if (!dist || !existsSync(join(dist, 'index.html'))) throw new Error('missing --dist <dir> (ordinary build)');
  mkdirSync(outDir, { recursive: true });
  const cases = [];
  const screenshots = [];
  let current = null;
  const beginCase = (id, title) => { current = { id, title, checks: [], pass: false }; cases.push(current); console.log(`\n=== ${id}: ${title}`); };
  const endCase = () => { current.pass = current.checks.length > 0 && current.checks.every((c) => c.ok); console.log(`--- ${current.id} ${current.pass ? 'PASS' : 'FAIL'}`); };
  function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    current.checks.push({ name, actual, expected, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)} expected=${JSON.stringify(expected)}`}`);
  }

  const joined = distJs(dist).map((f) => readFileSync(f, 'utf8')).join('\n');
  const fingerprint = {
    indexHtmlSha256: sha256(readFileSync(join(dist, 'index.html'))),
    preferenceMarkers: Object.fromEntries(PREFERENCE_MARKERS.map((s) => [s, joined.includes(s)])),
  };
  const { server, url: base } = await startServer(dist);
  const userDataDir = `/tmp/w3b2-${process.pid}`; // short: Chrome's singleton socket path limit
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
    ], { stdio: ['ignore', 'ignore', 'ignore'], env: { ...process.env, TMPDIR: userDataDir } });
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
      const other = await openPage(`${base}/__w3b_blank`);
      await evaluate(other, `(() => { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); return true; })()`);
      await closePage(other);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await waitTrue(page, langIs(locale), `storage-event switch to ${locale}`, 5000);
      await sleep(600); // quiescence: any switch-caused request lands in the ledger
    }

    if (focusedCascade) {
      beginCase('C9-Jobs', 'C9 Jobs cascade populated/loading/empty/error en/zh-CN 390/1440');
      for (const locale of ['en', 'zh-CN']) for (const [width, height] of [[390, 844], [1440, 900]]) for (const state of ['populated', 'loading', 'empty', 'error']) {
        const from = LEDGER.length;
        const page = await openPage(`${base}/orgs/${ORG}/jobs/JOB-901?cascadeFixture=${state}`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}`, width, height });
        const text = state === 'populated' ? 'Raw cascade task / 原文' : tr(locale, `jobs.cascade.${state === 'error' ? 'loadError' : state}`);
        check(`C9 Jobs ${locale} ${width} ${state} copy`, await waitTrue(page, bodyHas(text), 'actual cascade state'), true);
        check(`C9 Jobs ${locale} ${width} ${state} html lang`, await evaluate(page, langIs(locale)), true);
        check(`C9 Jobs ${locale} ${width} ${state} original query filter`, LEDGER.slice(from).some(row => row.path.endsWith('/tasks') && new URLSearchParams(row.search).get('blocked_on_job_id') === 'JOB-901'), true);
        check(`C9 Jobs ${locale} ${width} ${state} document bounds`, await evaluate(page, noOverflow), true);
        const card = `[...document.querySelectorAll('section')].find(s => s.textContent.includes(${JSON.stringify(text)}))`;
        check(`C9 Jobs ${locale} ${width} ${state} card text/control clipping and readability`, await evaluate(page, `(() => {
          const card = ${card}; if (!card) return { found: false };
          card.scrollIntoView({ block: 'center' });
          const errors = [];
          // Every row descendant (badge, waiting qualifier, ID link), not just the
          // row's direct children: text line boxes on BOTH axes, and a row label
          // must read as one line rather than a phrase stacked glyph by glyph.
          for (const el of card.querySelectorAll('h3,p,li span:not([aria-hidden]),a')) {
            const r = el.getBoundingClientRect(), css = getComputedStyle(el);
            if (!r.width || !r.height || r.left < 0 || r.right > innerWidth + 1 || Number.parseFloat(css.fontSize) < 10 || css.visibility !== 'visible' || css.opacity === '0') errors.push(el.tagName + ':bounds/readability');
            if (!el.classList.contains('truncate')) {
              const text = document.createRange(); text.selectNodeContents(el);
              const boxes = [...text.getClientRects()].filter(b => b.width > 0);
              if (boxes.some(b => b.left < r.left - 1 || b.right > r.right + 1 || b.left < 0 || b.right > innerWidth + 1)) errors.push(el.tagName + ':text bounds');
              if (boxes.some(b => b.top < r.top - 1 || b.bottom > r.bottom + 1 || b.top < 0 || b.bottom > innerHeight + 1)) errors.push(el.tagName + ':text vertical bounds');
              let lines = 0, lineBottom = -Infinity;
              for (const b of boxes.sort((x, y) => x.top - y.top)) {
                if (b.top >= lineBottom - 1) { lines += 1; lineBottom = b.bottom; } else lineBottom = Math.max(lineBottom, b.bottom);
              }
              if (el.closest('li') && lines > 1) errors.push(el.tagName + ':' + JSON.stringify(el.textContent) + ' split across ' + lines + ' lines');
            }
            for (let ancestor = el.parentElement; ancestor; ancestor = ancestor.parentElement) {
              const a = ancestor.getBoundingClientRect(), style = getComputedStyle(ancestor);
              if (/(hidden|clip|auto|scroll)/.test(style.overflowX) && (r.left < a.left - 1 || r.right > a.right + 1)) errors.push(el.tagName + ':clipping ancestor');
              if (/(hidden|clip|auto|scroll)/.test(style.overflowY) && (r.top < a.top - 1 || r.bottom > a.bottom + 1)) errors.push(el.tagName + ':vertical clipping ancestor');
            }
            if (el.matches('a')) { const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); if (hit !== el && !el.contains(hit)) errors.push('link:unreachable'); }
          }
          return { found: true, errors };
        })()`), { found: true, errors: [] });
        if (state === 'populated') {
          check(`C9 Jobs ${locale} ${width} owned waiting qualifier`, await evaluate(page, bodyHas(tr(locale, 'tasks.waiting.jobs'))), true);
          check(`C9 Jobs ${locale} ${width} raw status/brief`, await evaluate(page, `${bodyHas('in_progress')} && ${bodyHas('Raw cascade task / 原文')}`), true);
          const link = `document.querySelector('a[href="/orgs/${ORG}/tasks/TASK-CASE-J"]')`;
          check(`C9 Jobs ${locale} ${width} original task destination`, await evaluate(page, `Boolean(${link}) && ${link}.textContent === 'TASK-CASE-J'`), true);
          await capture(page, `c9-jobs-${locale}-${state}-${width}`, { locale, state, viewport: `${width}x${height}` });
          await clickSrc(page, link);
          check(`C9 Jobs ${locale} ${width} original navigation action`, await waitTrue(page, `location.pathname === '/orgs/${ORG}/tasks/TASK-CASE-J'`, 'task navigation'), true);
        } else await capture(page, `c9-jobs-${locale}-${state}-${width}`, { locale, state, viewport: `${width}x${height}` });
        await closePage(page);
      }
      endCase();
    } else {
    // ============================================================ G
    beginCase('G', 'ordinary bundle contains the Preferences selector (preview enabled, no build flag)');
    for (const s of PREFERENCE_MARKERS) check(`G ordinary JS contains "${s}"`, fingerprint.preferenceMarkers[s], true);
    endCase();

    // ============================================================ V
    beginCase('V', 'Jobs list + detail, en/zh-CN, 1440x900 + 390x844');
    for (const locale of ['en', 'zh-CN']) {
      const short = locale === 'en' ? 'en' : 'zh';
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        {
          const page = await openPage(`${base}/orgs/${ORG}/jobs`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}`, width: w, height: h });
          await waitTrue(page, `${bodyHas('JOB-901')} && ${bodyHas('JOB-904')}`, 'list rows');
          await sleep(400);
          check(`V list ${locale} ${w} <html lang>`, await evaluate(page, langIs(locale)), true);
          for (const key of ['jobs.list.eyebrow', 'jobs.list.title', 'jobs.list.needsYouBody', 'jobs.list.needsReview', 'jobs.list.column.requestedBy', 'jobs.group.pending', 'jobs.group.running', 'jobs.group.completed', 'jobs.group.rejected']) {
            check(`V list ${locale} ${w} shows ${key}`, await evaluate(page, bodyHas(tr(locale, key))), true);
          }
          check(`V list ${locale} ${w} callout count`, await evaluate(page, bodyHas(tr(locale, 'jobs.list.needsYou', { count: 1 }))), true);
          check(`V list ${locale} ${w} daemon values verbatim`, await evaluate(page, `${bodyHas(AUTH.title)} && ${bodyHas('$ ' + AUTH.script)} && ${bodyHas('devops_agent')} && ${bodyHas('TASK-640')} && ${bodyHas('exit 0')}`), true);
          check(`V list ${locale} ${w} no document horizontal overflow`, await evaluate(page, noOverflow), true);
          await capture(page, `${short}-jobs-list-${w}`, { viewport: `${w}x${h}`, locale, route: 'jobs' });
          await closePage(page);
        }
        {
          const page = await openPage(`${base}/orgs/${ORG}/jobs/JOB-901`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}`, width: w, height: h });
          await waitTrue(page, `${bodyHas(AUTH.title)} && ${bodyHas(tr(locale, 'jobs.cascade.empty'))}`, 'detail content');
          await sleep(400);
          check(`V detail ${locale} ${w} <html lang>`, await evaluate(page, langIs(locale)), true);
          for (const key of ['jobs.detail.commandEyebrow', 'jobs.detail.commandNote', 'jobs.cascade.title', 'jobs.gated.chip', 'jobs.gated.title', 'jobs.rail.requestedBy', 'jobs.rail.execution', 'jobs.rail.maxRuntime', 'jobs.action.approveRun', 'jobs.action.reject']) {
            check(`V detail ${locale} ${w} shows ${key}`, await evaluate(page, bodyHas(tr(locale, key))), true);
          }
          check(`V detail ${locale} ${w} back link`, await evaluate(page, bodyHas(tr(locale, 'jobs.detail.back', { taskId: 'TASK-640' }))), true);
          check(`V detail ${locale} ${w} seconds value`, await evaluate(page, bodyHas(tr(locale, 'jobs.rail.seconds', { value: '600' }))), true);
          check(`V detail ${locale} ${w} daemon values verbatim`, await evaluate(page, `${bodyHas(AUTH.script)} && ${bodyHas(AUTH.rationale)} && ${bodyHas('devops_agent')} && ${bodyHas('bash · cwd: repos/infra')} && ${bodyHas('pending')}`), true);
          check(`V detail ${locale} ${w} no document horizontal overflow`, await evaluate(page, noOverflow), true);
          await capture(page, `${short}-job-detail-${w}`, { viewport: `${w}x${h}`, locale, route: 'jobs/:job_id' });
          await closePage(page);
        }
      }
    }
    endCase();

    // ============================================================ S
    beginCase('S', 'Run-job dialog draft survives en -> zh-CN -> en: same nodes, value, focus, zero /api requests');
    {
      const page = await openPage(`${base}/orgs/${ORG}/jobs/JOB-901`, { init: `${seedLocale('en')}\n${CHINESE_NAVIGATOR}` });
      await waitTrue(page, bodyHas(AUTH.title), 'detail content');
      await sleep(400);
      const runButton = `[...document.querySelectorAll('header button')].find((b) => b.textContent.trim() === ${JSON.stringify(tr('en', 'jobs.action.approveRun'))})`;
      await clickSrc(page, runButton);
      const FIELD = `document.querySelector('[role="dialog"] input[type="text"]')`;
      await waitTrue(page, `Boolean(${FIELD})`, 'dialog');
      const DRAFT = 'repos/infra/staging — «draft» 中文';
      await evaluate(page, `(() => { ${FIELD}.focus(); return true; })()`);
      await cdp.send('Input.insertText', { text: DRAFT }, page.sessionId);
      await sleep(200);
      // dialog that owns the draft input.
      const DIALOG = `(${FIELD} || { closest: () => null }).closest('[role="dialog"]')`;
      await evaluate(page, `(() => { window.__w3bDialog = new WeakRef(${DIALOG}); window.__w3bField = new WeakRef(${FIELD}); return true; })()`);
      const state = `(() => {
        const d = window.__w3bDialog.deref(); const t = window.__w3bField.deref();
        const fd = ${DIALOG}; const ft = ${FIELD};
        const label = ft && ft.id ? document.querySelector('label[for="' + ft.id + '"]') : null;
        return {
          sameDialog: Boolean(d && d === fd && d.isConnected && d.contains(t)), sameField: Boolean(t && t === ft && t.isConnected),
          value: t ? t.value : null, focused: document.activeElement === t,
          title: fd && document.getElementById(fd.getAttribute('aria-labelledby')) ? document.getElementById(fd.getAttribute('aria-labelledby')).textContent : null,
          label: label ? label.textContent : null, lang: document.documentElement.lang,
        };
      })()`;
      check('S before switch: value + focus', await evaluate(page, `(() => { const t = window.__w3bField.deref(); return [t.value, document.activeElement === t]; })()`), [DRAFT, true]);
      for (const locale of ['zh-CN', 'en']) {
        const from = LEDGER.length;
        await crossTabSwitch(page, locale);
        const s = await evaluate(page, state);
        check(`S -> ${locale} same dialog node`, s.sameDialog, true);
        check(`S -> ${locale} same input node`, s.sameField, true);
        check(`S -> ${locale} draft value kept`, s.value, DRAFT);
        check(`S -> ${locale} focus kept`, s.focused, true);
        check(`S -> ${locale} dialog title localized`, s.title, tr(locale, 'jobs.run.titleApprove', { jobId: 'JOB-901' }));
        check(`S -> ${locale} field label localized`, s.label, tr(locale, 'jobs.run.cwdOverride'));
        check(`S -> ${locale} <html lang>`, s.lang, locale);
        check(`S -> ${locale} zero /api requests in switch window`, LEDGER.slice(from), []);
        if (locale === 'zh-CN') await capture(page, 'zh-run-dialog-draft-1440', { viewport: '1440x900', locale, state: 'Run dialog draft after en->zh-CN switch' });
      }
      await closePage(page);
    }
    endCase();

    // ============================================================ P
    beginCase('P', 'Preferences in the ordinary build: unset follows Chinese browser fallback; disclosure; zh-CN switch in place with zero /api');
    {
      // Earlier cases share this profile's localStorage: remove the saved
      // preference before the app boots so this page starts genuinely UNSET.
      const unset = `try { localStorage.removeItem(${JSON.stringify(LOCALE_KEY)}); } catch (e) {}`;
      const page = await openPage(`${base}/orgs/${ORG}/settings/preferences`, { init: `${unset}\n${CHINESE_NAVIGATOR}` });
      await waitTrue(page, `Boolean(document.querySelector('[data-testid="settings-preferences"]'))`, 'preferences panel');
      await sleep(400);
      const radio = (value) => `document.querySelector('input[name="happyranch-ui-language"][value="${value}"]')`;
      check('P unset: stored preference absent', await evaluate(page, `localStorage.getItem(${JSON.stringify(LOCALE_KEY)})`), null);
      check('P unset: navigator is Chinese', await evaluate(page, `navigator.languages.join(',')`), 'zh-CN,zh');
      check('P unset: <html lang> zh-CN', await evaluate(page, langIs('zh-CN')), true);
      check('P unset: Chinese radio checked', await evaluate(page, `${radio('zh-CN')}.checked`), true);
      check('P unset: route stays /settings/preferences', await evaluate(page, `location.pathname`), `/orgs/${ORG}/settings/preferences`);
      check('P unset: disclosure (zh-CN) visible', await evaluate(page, `(() => { const p = [...document.querySelectorAll('[data-testid="settings-preferences"] p')].find((n) => n.textContent === ${JSON.stringify(tr('zh-CN', 'settings.preferences.coverageDisclosure'))}); return Boolean(p && p.getBoundingClientRect().height > 0); })()`), true);
      await capture(page, 'zh-preferences-unset-1440', { viewport: '1440x900', locale: 'zh-CN', state: 'unset preference on zh-CN browser' });
      await clickSrc(page, radio('en'));
      check('P explicit English prepares original Chinese switch', await waitTrue(page, `${langIs('en')} && ${radio('en')}.checked`, 'explicit English before Chinese switch'), true);
      await evaluate(page, `(() => { window.__w3bRadio = new WeakRef(${radio('zh-CN')}); window.__w3bPanel = new WeakRef(document.querySelector('[data-testid="settings-preferences"]')); return true; })()`);
      const from = LEDGER.length;
      await clickSrc(page, radio('zh-CN'));
      await waitTrue(page, langIs('zh-CN'), 'radio switch to zh-CN', 5000);
      await sleep(600);
      check('P switch: <html lang> zh-CN', await evaluate(page, langIs('zh-CN')), true);
      check('P switch: same radio node, checked + focused', await evaluate(page, `(() => { const r = window.__w3bRadio.deref(); return [r === ${radio('zh-CN')}, r.checked, document.activeElement === r]; })()`), [true, true, true]);
      check('P switch: same panel node', await evaluate(page, `window.__w3bPanel.deref() === document.querySelector('[data-testid="settings-preferences"]')`), true);
      check('P switch: disclosure (zh-CN)', await evaluate(page, bodyHas(tr('zh-CN', 'settings.preferences.coverageDisclosure'))), true);
      check('P switch: heading (zh-CN)', await evaluate(page, bodyHas(tr('zh-CN', 'settings.panel.preferences.title'))), true);
      check('P switch: stored preference zh-CN', await evaluate(page, `localStorage.getItem(${JSON.stringify(LOCALE_KEY)})`), 'zh-CN');
      check('P switch: zero /api requests in switch window', LEDGER.slice(from), []);
      await capture(page, 'zh-preferences-selected-1440', { viewport: '1440x900', locale: 'zh-CN', state: 'after selecting 简体中文' });
      await closePage(page);
    }
    endCase();
    }
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
    chrome: { path: chromeBin, version: chromeVersion, lang: 'zh-CN (Chrome --lang + actual navigator override; full-mode fallback)' },
    externalRequestsBlocked: EXTERNAL_BLOCK,
    dist: fingerprint,
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
