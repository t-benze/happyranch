#!/usr/bin/env node
/**
 * W3b-1 Tasks browser-evidence harness (THR-118).
 *
 * A deliberately small sibling of `w3a-core-browser-evidence.mjs` (same
 * mechanism, no new dependency): ONE isolated headless Chrome driven over the
 * DevTools Protocol against the ORDINARY production SPA bundle, served
 * same-origin next to a synthetic `/api/v1` stub whose every request lands in a
 * server-side ledger. The app carries no evidence instrumentation: locale
 * switches use the supported `happyranch.ui.locale` preference written by a
 * second same-origin tab (the `storage` event path), because the Preferences
 * selector stays gated closed. Expected copy is read from the shipped typed
 * catalogs (Node 24 strips the TS types).
 *
 * Cases (receipt.json; exit 1 if any fails):
 *   G  the ordinary bundle excludes the Preferences gate markers;
 *   V  Tasks list + task detail in en and zh-CN at 1440x900 and 390x844:
 *      <html lang>, localized chrome, authored brief/IDs/agent verbatim, no
 *      document-level horizontal overflow, PNG + sha256;
 *   S  one state-preservation check: open the Cancel-task dialog, type a draft,
 *      switch en -> zh-CN -> en; the SAME dialog and textarea nodes, the same
 *      draft value and focus, and ZERO /api requests in each switch window.
 *
 * Build + run (from web/):
 *   ./node_modules/.bin/vite build --outDir <tmp>/dist-ordinary
 *   node scripts/w3b-tasks-browser-evidence.mjs --dist <tmp>/dist-ordinary \
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
const outDir = resolve(arg('out', '.w3b-tasks-evidence'));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

const ORG = 'test-org';
const LOCALE_KEY = 'happyranch.ui.locale';
const GATED_STRINGS = ['settings-preferences', 'happyranch-ui-language'];
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
// Authored/raw fixture bytes that must survive every locale unchanged.
const AUTH = {
  brief: 'Ship the v2 importer — «raw» `jobs_v2` migration (authored brief)',
  note: 'raw daemon note: refund_policy unresolved',
  job: 'Nightly e2e on staging (authored job title)',
};
const now = Date.now();
const iso = (msAgo) => new Date(now - msAgo).toISOString();
const LEDGER = [];
const HUNG = [];

function task(id, extra = {}) {
  return {
    task_id: id, team: 'engineering', brief: AUTH.brief, status: 'in_progress', block_kind: 'delegated',
    assigned_agent: 'dev_agent', parent_task_id: null, revisit_of_task_id: null,
    created_at: iso(3 * 3600e3), updated_at: iso(5 * 60e3), closed_at: null, cancelled_at: null,
    session_timeout_seconds: null, severity_rollup: 'failed', dispatched_from_thread_id: 'THR-101', ...extra,
  };
}
const ROOTS = [
  task('TASK-501'),
  task('TASK-502', { status: 'completed', block_kind: null, severity_rollup: 'completed', brief: 'Docs refresh for importer', assigned_agent: 'qa_engineer', dispatched_from_thread_id: null, updated_at: iso(26 * 3600e3) }),
  task('TASK-503', { status: 'pending', block_kind: null, severity_rollup: 'pending', brief: 'Queue capacity review', assigned_agent: null, updated_at: iso(2 * 3600e3) }),
];
const ESCALATED = [task('TASK-500', { status: 'escalated', block_kind: null, severity_rollup: 'escalated', brief: 'Approve schema change for `jobs_v2`?', note: AUTH.note })];
const DETAIL = task('TASK-501', { revisit_of_task_id: 'TASK-490' });

function api(pathname, search) {
  if (pathname === '/api/v1/auth/bootstrap') return { token: 'w3b-evidence-token' };
  if (pathname === '/api/v1/orgs') return { orgs: [{ slug: ORG, root: `/runtime/${ORG}` }], broken: [] };
  if (pathname === `/api/v1/orgs/${ORG}/dashboard/summary`) return { org_age_days: 12 };
  if (pathname === `/api/v1/orgs/${ORG}/tasks/roots`) {
    return { tasks: new URLSearchParams(search).get('status') === 'escalated' ? ESCALATED : ROOTS, next_cursor: null };
  }
  if (pathname === `/api/v1/orgs/${ORG}/tasks/TASK-501`) {
    return {
      task: DETAIL, results: [], revisit_chain: ['TASK-501', 'TASK-490'], direct_revisits: [],
      predecessor_prior_status: null,
      audit_log: [],
      active_chain: { step_index: 1, first_leg_expect_verdict: 'APPROVE', legs: [{ agent: 'qa_engineer', expect_verdict: 'PASS' }] },
      work_status: {
        applicable: true, state: 'recent_progress', label: 'Recent update recorded', reason: null,
        session_start_ts: iso(40 * 60e3), heartbeat: { timestamp: iso(20e3), freshness: 'fresh' },
        latest_progress: { timestamp: iso(3 * 60e3), message: 'Ported 3 of 5 tables (agent-authored)', agent: 'dev_agent' },
      },
    };
  }
  if (pathname === `/api/v1/orgs/${ORG}/tasks/TASK-501/recall`) {
    return {
      task_id: 'TASK-501', assigned_agent: 'dev_agent', brief: AUTH.brief, status: 'in_progress', output_summary: null,
      children: [
        { task_id: 'TASK-511', assigned_agent: 'qa_engineer', brief: 'Verify importer on staging', status: 'failed', output_summary: 'Two rows mismatched (authored summary)', children: [] },
        { task_id: 'TASK-512', assigned_agent: 'dev_agent', brief: 'Port remaining tables', status: 'in_progress', output_summary: null, children: [] },
      ],
    };
  }
  if (pathname === `/api/v1/orgs/${ORG}/jobs/` || pathname === `/api/v1/orgs/${ORG}/jobs`) {
    return { jobs: [{ id: 'JOB-77', title: AUTH.job, status: 'running', task_id: 'TASK-501' }], next_cursor: null };
  }
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
    gatedStrings: Object.fromEntries(GATED_STRINGS.map((s) => [s, joined.includes(s)])),
  };
  const { server, url: base } = await startServer(dist);
  const userDataDir = `/tmp/w3b-${process.pid}`; // short: Chrome's singleton socket path limit
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
      for (const domain of ['Page', 'Runtime']) await cdp.send(`${domain}.enable`, {}, sessionId);
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

    // ============================================================ G
    beginCase('G', 'ordinary bundle excludes the Preferences gate markers');
    for (const s of GATED_STRINGS) check(`G ordinary JS lacks "${s}"`, fingerprint.gatedStrings[s], false);
    endCase();

    // ============================================================ V
    beginCase('V', 'Tasks list + detail, en/zh-CN, 1440x900 + 390x844');
    for (const locale of ['en', 'zh-CN']) {
      const short = locale === 'en' ? 'en' : 'zh';
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        // List
        {
          const page = await openPage(`${base}/orgs/${ORG}/tasks`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}`, width: w, height: h });
          await waitTrue(page, `${bodyHas('TASK-501')} && ${bodyHas('TASK-500')}`, 'list rows');
          await sleep(400);
          check(`V list ${locale} ${w} <html lang>`, await evaluate(page, langIs(locale)), true);
          for (const key of ['tasks.page.title', 'tasks.attention.heading', 'tasks.group.status.inProgress', 'tasks.group.status.completed', 'tasks.waiting.subtasks', 'tasks.page.eyebrow.rollUp']) {
            check(`V list ${locale} ${w} shows ${key}`, await evaluate(page, bodyHas(tr(locale, key))), true);
          }
          check(`V list ${locale} ${w} subtask rollup`, await evaluate(page, bodyHas(tr(locale, 'tasks.row.subtaskRollup', { status: tr(locale, 'tasks.rollup.failed') }))), true);
          check(`V list ${locale} ${w} authored brief verbatim`, await evaluate(page, bodyHas(AUTH.brief)), true);
          check(`V list ${locale} ${w} agent/thread ids verbatim`, await evaluate(page, `${bodyHas('dev_agent')} && ${bodyHas('THR-101')}`), true);
          check(`V list ${locale} ${w} no document horizontal overflow`, await evaluate(page, noOverflow), true);
          await capture(page, `${short}-tasks-list-${w}`, { viewport: `${w}x${h}`, locale, route: 'tasks' });
          await closePage(page);
        }
        // Detail
        {
          const page = await openPage(`${base}/orgs/${ORG}/tasks/TASK-501`, { init: `${seedLocale(locale)}\n${CHINESE_NAVIGATOR}`, width: w, height: h });
          await waitTrue(page, `${bodyHas('TASK-511')} && ${bodyHas('JOB-77')} && ${bodyHas(tr(locale, 'tasks.exec.heading'))}`, 'detail content');
          await sleep(400);
          check(`V detail ${locale} ${w} <html lang>`, await evaluate(page, langIs(locale)), true);
          for (const key of ['tasks.detail.back', 'tasks.detail.revisit', 'tasks.detail.brief.heading', 'tasks.detail.lineage.heading', 'tasks.detail.subtasks.heading', 'tasks.detail.recall.heading', 'tasks.detail.activity.heading', 'tasks.detail.jobs.heading', 'tasks.rail.heading', 'tasks.exec.label.recentProgress', 'tasks.detail.chain.firstLeg']) {
            check(`V detail ${locale} ${w} shows ${key}`, await evaluate(page, bodyHas(tr(locale, key))), true);
          }
          check(`V detail ${locale} ${w} chain heading`, await evaluate(page, bodyHas(tr(locale, 'tasks.detail.chain.heading', { step: 2, total: 2 }))), true);
          check(`V detail ${locale} ${w} authored values verbatim`, await evaluate(page, `${bodyHas('TASK-490')} && ${bodyHas(AUTH.job)} && ${bodyHas('Ported 3 of 5 tables (agent-authored)')} && ${bodyHas('Two rows mismatched (authored summary)')} && ${bodyHas('(running)')} && ${bodyHas('APPROVE')}`), true);
          check(`V detail ${locale} ${w} no document horizontal overflow`, await evaluate(page, noOverflow), true);
          await capture(page, `${short}-task-detail-${w}`, { viewport: `${w}x${h}`, locale, route: 'tasks/:task_id' });
          await closePage(page);
        }
      }
    }
    endCase();

    // ============================================================ S
    beginCase('S', 'Cancel-task dialog draft survives en -> zh-CN -> en: same nodes, value, focus, zero /api requests');
    {
      const page = await openPage(`${base}/orgs/${ORG}/tasks/TASK-501`, { init: `${seedLocale('en')}\n${CHINESE_NAVIGATOR}` });
      await waitTrue(page, bodyHas('TASK-511'), 'detail content');
      await sleep(400);
      const cancelButton = `[...document.querySelectorAll('header button')].find((b) => b.textContent.trim() === ${JSON.stringify(tr('en', 'tasks.detail.cancel'))})`;
      await clickSrc(page, cancelButton);
      await waitTrue(page, `Boolean(document.querySelector('[role="dialog"] textarea'))`, 'dialog');
      const DRAFT = 'Draft reason — keep «exactly» 中文 too';
      await evaluate(page, `(() => { const t = document.querySelector('[role="dialog"] textarea'); t.focus(); return true; })()`);
      await cdp.send('Input.insertText', { text: DRAFT }, page.sessionId);
      await sleep(200);
      await evaluate(page, `(() => { window.__w3bDialog = new WeakRef(document.querySelector('[role="dialog"]')); window.__w3bField = new WeakRef(document.querySelector('[role="dialog"] textarea')); return true; })()`);
      const state = `(() => {
        const d = window.__w3bDialog.deref(); const t = window.__w3bField.deref();
        const fd = document.querySelector('[role="dialog"]'); const ft = document.querySelector('[role="dialog"] textarea');
        return {
          sameDialog: Boolean(d && d === fd && d.isConnected), sameField: Boolean(t && t === ft && t.isConnected),
          value: t ? t.value : null, focused: document.activeElement === t,
          title: fd ? (fd.querySelector('h2') || {}).textContent : null,
          placeholder: ft ? ft.placeholder : null, lang: document.documentElement.lang,
        };
      })()`;
      check('S before switch: value + focus', await evaluate(page, `(() => { const t = window.__w3bField.deref(); return [t.value, document.activeElement === t]; })()`), [DRAFT, true]);
      for (const locale of ['zh-CN', 'en']) {
        const from = LEDGER.length;
        await crossTabSwitch(page, locale);
        const s = await evaluate(page, state);
        check(`S -> ${locale} same dialog node`, s.sameDialog, true);
        check(`S -> ${locale} same textarea node`, s.sameField, true);
        check(`S -> ${locale} draft value kept`, s.value, DRAFT);
        check(`S -> ${locale} focus kept`, s.focused, true);
        check(`S -> ${locale} dialog title localized`, s.title, tr(locale, 'tasks.dialog.cancel.title'));
        check(`S -> ${locale} placeholder localized`, s.placeholder, tr(locale, 'tasks.dialog.cancel.placeholder'));
        check(`S -> ${locale} <html lang>`, s.lang, locale);
        check(`S -> ${locale} zero /api requests in switch window`, LEDGER.slice(from), []);
        if (locale === 'zh-CN') await capture(page, 'zh-cancel-dialog-draft-1440', { viewport: '1440x900', locale, state: 'dialog draft after en->zh-CN switch' });
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
