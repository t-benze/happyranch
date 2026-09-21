#!/usr/bin/env node
/**
 * W2a shell browser-evidence harness (THR-118).
 *
 * Drives ONE isolated headless Chrome over the DevTools Protocol (no new
 * dependency: Node 24's built-in WebSocket + the already-installed Chrome)
 * against the REAL production SPA bundle (`web/dist`) served same-origin next
 * to a synthetic `/api/v1` stub. The bundle is built with
 * `I18N_W2A_EVIDENCE=1`, which makes `vite.config.ts` inject the test-only
 * `ShellEvidenceConsumer` next to `<AppRoutes />` and `ShellErrorTrigger`
 * inside the real `AppShellErrorBoundary`.
 *
 * It asserts, in both locales:
 *   - first-commit shell copy (`html.lang` + AppBar title + Sidebar nav labels);
 *   - the mounted shell DOM (nav/aria/org switcher/account/not-found/loading);
 *   - stable switching with typed slug / open help tab / typed palette query;
 *   - localized ErrorBoundary fallback with the raw stack preserved;
 *   - generated PNG screenshots at 1440x900 and 390x844, light and dark.
 *
 * Usage:
 *   I18N_W2A_EVIDENCE=1 npm run build
 *   node scripts/w2a-shell-browser-evidence.mjs --dist ./dist \
 *     --out <evidence dir> --head <40-char-sha>
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, '..');

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 || index + 1 >= process.argv.length ? fallback : process.argv[index + 1];
}

const distDir = resolve(arg('dist', join(webRoot, 'dist')));
const outDir = resolve(arg('out', join(webRoot, '.w2a-shell-evidence')));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ttf': 'font/ttf',
  '.ico': 'image/x-icon',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
};

const DEV_ENVIRONMENTS = {
  zh: { id: 'zh-CN', locale: 'zh-CN', language: 'zh-CN', languages: ['zh-CN', 'zh'] },
  en: { id: 'en-US', locale: 'en-US', language: 'en-US', languages: ['en-US', 'en'] },
};

/** Mutable synthetic-API knobs (single sequential harness, so module scope is fine). */
let syntheticNoOrg = false;
let syntheticOrgDelayMs = 0;

const SUMMARY = {
  heartbeat: [],
  narrative_counts: {
    completed_today: 0,
    failed_today: 0,
    escalated_open: 0,
    kb_added_today: 0,
    agents_active_now: 0,
    spend_today_usd: 0,
  },
  escalations: [],
  active_by_team: [],
  recent_activity: [],
  updates_this_week: [],
  org_pulse: [],
  org_age_days: 3,
  server_now: '2026-06-17T12:00:00Z',
  generated_at: '2026-06-17T12:00:00Z',
};

function apiBody(pathname) {
  if (pathname === '/api/v1/auth/bootstrap') return { token: 'w2a-evidence-token' };
  if (pathname === '/api/v1/orgs') {
    return syntheticNoOrg ? { orgs: [], broken: [] } : { orgs: [{ slug: 'demo-org', root: '/x' }] };
  }
  if (/\/dashboard\/summary$/.test(pathname)) return SUMMARY;
  if (/\/agents$/.test(pathname)) return { agents: [] };
  if (/\/threads\/events$/.test(pathname)) return {};
  if (/\/threads$/.test(pathname)) return { threads: [] };
  if (/\/tokens$/.test(pathname)) return { rollup: [] };
  if (/\/dreams$/.test(pathname)) return { dreams: [] };
  if (pathname === '/api/v1/health') return { status: 'ok', active_runtime: '/x' };
  return {};
}

function startServer({ root, api }) {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        if (api && url.pathname.startsWith('/api/')) {
          const respond = () => {
            response.writeHead(200, {
              'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            });
            response.end(JSON.stringify(apiBody(url.pathname)));
          };
          if (url.pathname === '/api/v1/orgs' && syntheticOrgDelayMs > 0) {
            setTimeout(respond, syntheticOrgDelayMs);
          } else {
            respond();
          }
          return;
        }
        const relative = decodeURIComponent(url.pathname);
        let file = relative === '/' || relative === '' ? join(root, 'index.html') : join(root, relative);
        if (!existsSync(file) || statSync(file).isDirectory()) file = join(root, 'index.html');
        response.writeHead(200, {
          'content-type': MIME[extname(file)] || 'application/octet-stream',
          'cache-control': 'no-store',
        });
        response.end(readFileSync(file));
      } catch (error) {
        response.writeHead(500);
        response.end(String(error));
      }
    });
    server.on('error', rejectPromise);
    server.listen(0, '127.0.0.1', () => resolvePromise({ server, port: server.address().port }));
  });
}

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    this.ready = new Promise((resolvePromise, rejectPromise) => {
      this.ws.addEventListener('open', () => resolvePromise());
      this.ws.addEventListener('error', () => rejectPromise(new Error('CDP websocket error')));
    });
    this.ws.addEventListener('message', (event) => {
      const text = typeof event.data === 'string' ? event.data : Buffer.from(event.data).toString('utf8');
      const message = JSON.parse(text);
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
        return;
      }
      if (message.method) {
        for (const handler of [...(this.handlers.get(message.method) || [])]) handler(message);
      }
    });
  }

  async send(method, params = {}, sessionId) {
    await this.ready;
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    const promise = new Promise((resolvePromise, rejectPromise) => {
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise });
    });
    this.ws.send(JSON.stringify(payload));
    return promise;
  }

  waitFor(method, { sessionId, timeout = 30000 } = {}) {
    return new Promise((resolvePromise, rejectPromise) => {
      const handler = (message) => {
        if (sessionId && message.sessionId !== sessionId) return;
        off();
        clearTimeout(timer);
        resolvePromise(message.params);
      };
      const list = this.handlers.get(method) || [];
      list.push(handler);
      this.handlers.set(method, list);
      const off = () => {
        const current = this.handlers.get(method) || [];
        this.handlers.set(method, current.filter((candidate) => candidate !== handler));
      };
      const timer = setTimeout(() => {
        off();
        rejectPromise(new Error(`timeout waiting for CDP ${method}`));
      }, timeout);
    });
  }

  close() {
    try {
      this.ws.close();
    } catch {
      /* already closed */
    }
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

function pickProfileRoot() {
  for (const base of ['/tmp', tmpdir()]) {
    try {
      mkdirSync(base, { recursive: true });
      const probe = join(base, `.hr-w2a-probe-${process.pid}`);
      writeFileSync(probe, '');
      rmSync(probe, { force: true });
      return base;
    } catch {
      /* next */
    }
  }
  throw new Error('no writable temp root for the Chrome profile');
}

function navigatorLocaleSource(env) {
  return `
(() => {
  const expected = ${JSON.stringify({ language: env.language, languages: env.languages })};
  const failures = [];
  try { Object.defineProperty(Navigator.prototype, 'language', { configurable: true, get: () => expected.language }); }
  catch (error) { failures.push('define language: ' + (error && error.message)); }
  try { Object.defineProperty(Navigator.prototype, 'languages', { configurable: true, get: () => expected.languages.slice() }); }
  catch (error) { failures.push('define languages: ' + (error && error.message)); }
  let actualLanguage = null, actualLanguages = null;
  try { actualLanguage = navigator.language; } catch (error) { failures.push('read language'); }
  try { actualLanguages = Array.isArray(navigator.languages) ? navigator.languages.slice() : null; } catch (error) { failures.push('read languages'); }
  window.__hrLocaleSetup = {
    ok: failures.length === 0 && actualLanguage === expected.language && Array.isArray(actualLanguages) && actualLanguages.join(',') === expected.languages.join(','),
    expected,
    actual: { language: actualLanguage, languages: actualLanguages },
    failures,
  };
})();
`;
}

function seedLocale(locale) {
  return `try { localStorage.setItem('happyranch.ui.locale', ${JSON.stringify(locale)}); } catch (error) {}`;
}
function clearLocale() {
  return `try { localStorage.removeItem('happyranch.ui.locale'); } catch (error) {}`;
}
function seedTheme(theme) {
  return `try { localStorage.setItem('happyranch.theme', ${JSON.stringify(theme)}); } catch (error) {}`;
}

async function main() {
  if (!existsSync(distDir)) throw new Error(`missing build directory ${distDir}; run the W2a web build first`);
  mkdirSync(outDir, { recursive: true });

  const assertions = [];
  const screenshots = [];
  const notes = [];
  const environments = [];

  function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    assertions.push({ name, actual, expected, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)} expected=${JSON.stringify(expected)}`}`);
    return ok;
  }
  function checkIncludes(name, actual, needle) {
    const ok = typeof actual === 'string' && actual.includes(needle);
    assertions.push({ name, actual, expected: `includes ${needle}`, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)}`}`);
    return ok;
  }
  function checkTruthy(name, actual) {
    const ok = Boolean(actual);
    assertions.push({ name, actual, expected: 'truthy', ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)}`}`);
    return ok;
  }

  const appServer = await startServer({ root: distDir, api: true });
  const appUrl = `http://127.0.0.1:${appServer.port}/`;
  notes.push({ appUrl });

  const userDataDir = join(pickProfileRoot(), `hr-w2a-${process.pid}-${Date.now()}`);
  mkdirSync(userDataDir, { recursive: true });

  let chrome;
  let chromeStderr = '';
  let cdp;
  try {
    chrome = spawn(
      chromeBin,
      [
        '--headless=new',
        '--remote-debugging-port=0',
        `--user-data-dir=${userDataDir}`,
        `--crash-dumps-dir=${join(userDataDir, 'crash')}`,
        '--disable-crash-reporter',
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--disable-extensions',
        '--disable-background-networking',
        '--hide-scrollbars',
        'about:blank',
      ],
      {
        stdio: ['ignore', 'ignore', 'pipe'],
        env: {
          ...process.env,
          HOME: userDataDir,
          TMPDIR: userDataDir,
          TMP: userDataDir,
          TEMP: userDataDir,
          XDG_CONFIG_HOME: join(userDataDir, '.config'),
          XDG_CACHE_HOME: join(userDataDir, '.cache'),
          XDG_DATA_HOME: join(userDataDir, '.local', 'share'),
        },
      },
    );
    chrome.stderr.on('data', (chunk) => {
      chromeStderr += chunk.toString();
    });
    chrome.on('exit', (code) => {
      chromeStderr += `\n[chrome exited ${code}]`;
    });

    const devtools = await waitForDevTools(userDataDir);
    const version = await (await fetch(`http://127.0.0.1:${devtools.port}/json/version`)).json();
    cdp = new CDP(`ws://127.0.0.1:${devtools.port}${devtools.path}`);
    await cdp.ready;
    notes.push({ chrome: version.Browser, chromeBin });

    async function openPage({ url, env, initScript, width = 1440, height = 900 }) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      await cdp.send('Page.enable', {}, sessionId);
      await cdp.send('Runtime.enable', {}, sessionId);
      await cdp.send('Network.enable', {}, sessionId);
      await cdp.send('DOM.enable', {}, sessionId);
      await cdp.send('CSS.enable', {}, sessionId);
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: width < 600,
      }, sessionId);
      await cdp.send('Network.setBlockedURLs', {
        urls: ['https://fonts.googleapis.com/*', 'https://fonts.gstatic.com/*'],
      }, sessionId);
      if (env) {
        try {
          await cdp.send('Emulation.setLocaleOverride', { locale: env.locale }, sessionId);
        } catch {
          /* non-fatal */
        }
      }
      const source = [env ? navigatorLocaleSource(env) : null, initScript].filter(Boolean).join('\n');
      if (source.trim()) await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source }, sessionId);
      const loaded = cdp.waitFor('Page.loadEventFired', { sessionId });
      await cdp.send('Page.navigate', { url }, sessionId);
      await loaded;
      return { targetId, sessionId };
    }

    async function closePage(page) {
      try {
        await cdp.send('Target.closeTarget', { targetId: page.targetId });
      } catch {
        /* already closed */
      }
    }

    async function evaluate(sessionId, expression) {
      const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, sessionId);
      if (result.exceptionDetails) {
        throw new Error(`evaluate failed: ${result.exceptionDetails.text} ${result.exceptionDetails.exception?.description || ''}`);
      }
      return result.result.value;
    }

    async function waitForValue(sessionId, expression, { timeout = 20000, label } = {}) {
      const deadline = Date.now() + timeout;
      let last;
      while (Date.now() < deadline) {
        try {
          last = await evaluate(sessionId, expression);
        } catch (error) {
          last = `error: ${error.message}`;
        }
        if (last) return last;
        await sleep(100);
      }
      throw new Error(`timeout waiting for ${label || expression}; last=${JSON.stringify(last)}`);
    }

    async function assertLocaleSetup(sessionId, label, env) {
      const setup = await evaluate(sessionId, 'window.__hrLocaleSetup || null');
      const ok = Boolean(setup && setup.ok);
      assertions.push({
        name: `${label} navigator language environment asserted before app modules`,
        actual: setup ? setup.actual : null,
        expected: { language: env.language, languages: env.languages },
        ok,
      });
      console.log(`${ok ? 'PASS' : 'FAIL'} ${label} navigator language environment asserted before app modules`);
      environments.push({ label, id: env.id, actual: setup ? setup.actual : null, ok });
      return setup;
    }

    async function clickSelector(sessionId, selector) {
      const { root } = await cdp.send('DOM.getDocument', { depth: -1 }, sessionId);
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector }, sessionId);
      if (!nodeId) throw new Error(`clickSelector: not found ${selector}`);
      const { model } = await cdp.send('DOM.getBoxModel', { nodeId }, sessionId);
      const x = (model.content[0] + model.content[4]) / 2;
      const y = (model.content[1] + model.content[5]) / 2;
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, sessionId);
      await sleep(200);
    }

    async function setInputValue(sessionId, selector, value) {
      return evaluate(
        sessionId,
        `(() => {
          const el = document.querySelector(${JSON.stringify(selector)});
          if (!el) return null;
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(el, ${JSON.stringify(value)});
          el.dispatchEvent(new Event('input', { bubbles: true }));
          return el.value;
        })()`,
      );
    }

    const SNAP = `(() => {
      const nav = document.querySelector('[role="navigation"][aria-label="主导航"], [role="navigation"][aria-label="Primary navigation"]');
      const navItems = nav ? nav.querySelector('nav') : null;
      const links = navItems ? [...navItems.querySelectorAll('a')].map((a) => ({ text: a.textContent.trim(), href: a.getAttribute('href') })) : [];
      const appBarTitle = document.querySelector('main')?.previousElementSibling?.querySelector('span')?.textContent ?? null;
      const bodyText = document.body.textContent || '';
      const notFoundLink = document.querySelector('main a[href="/"]');
      const account = document.querySelector('[aria-label^="Account:"], [aria-label^="账户"]');
      const themeBtn = document.querySelector('button[aria-label*="theme"], button[aria-label*="主题"]');
      const activeOrg = document.querySelector('[aria-label="Active org"], [aria-label="当前组织"]');
      const dialogs = [...document.querySelectorAll('[role="dialog"]')].map((d) => d.textContent);
      return {
        lang: document.documentElement.getAttribute('lang'),
        htmlTheme: document.documentElement.getAttribute('data-theme'),
        firstShell: window.__hrFirstShell || null,
        appBarTitle,
        navLinks: links,
        navAria: nav ? nav.getAttribute('aria-label') : null,
        accountLabel: account ? account.getAttribute('aria-label') : null,
        themeLabel: themeBtn ? themeBtn.getAttribute('aria-label') : null,
        activeOrg: activeOrg ? activeOrg.getAttribute('aria-label') : null,
        bodyText,
        notFoundLink: notFoundLink ? notFoundLink.textContent.trim() : null,
        dialogTexts: dialogs,
      };
    })()`;

    async function snapshot(sessionId) {
      return evaluate(sessionId, SNAP);
    }

    /**
     * Objective layout/glyph probe used where a human visual pass is not
     * available: real used platform fonts for CJK copy, document/body
     * horizontal overflow, nav-link viewport containment and dialog bounds.
     */
    const GEOMETRY = `(() => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const nav = document.querySelector('nav[aria-label="主导航项"], nav[aria-label="Primary navigation items"]');
      const links = nav ? [...nav.querySelectorAll('a')] : [];
      const outside = links.filter((a) => {
        const r = a.getBoundingClientRect();
        return r.right > vw + 1 || r.left < -1 || r.bottom > vh + 1 || r.top < -1;
      }).length;
      const dialogEls = [...document.querySelectorAll('[role="dialog"]')];
      const dialogs = dialogEls.map((d) => {
        const r = d.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height, text: (d.textContent || '').slice(0, 24) };
      });
      const visibleDialogs = dialogs.filter((d) => d.bottom > 0 && d.top < vh && d.right > 0 && d.left < vw);
      const dialogRect = visibleDialogs.length
        ? visibleDialogs.reduce((a, b) => (a.width * a.height >= b.width * b.height ? a : b))
        : null;
      return {
        vw, vh,
        docScrollWidth: document.documentElement.scrollWidth,
        bodyScrollWidth: document.body.scrollWidth,
        outside,
        dialogRect,
        dialogs,
      };
    })()`;

    async function geometry(sessionId) {
      return evaluate(sessionId, GEOMETRY);
    }

    async function platformFontsForSelector(sessionId, selector) {
      const { root } = await cdp.send('DOM.getDocument', { depth: -1 }, sessionId);
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector }, sessionId);
      if (!nodeId) return [];
      const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId }, sessionId);
      return fonts || [];
    }

    function isCjkFamily(name) {
      return /CJK|Han|Hei|Song|WenQuanYi|Noto Sans SC|Noto Serif SC|PingFang|Hiragino|YaHei|Microsoft YaHei/i.test(
        name || '',
      );
    }

    async function hideControls(sessionId) {
      await evaluate(
        sessionId,
        `(() => {
          for (const sel of ['[data-testid="w2a-evidence-controls"]', '[data-testid="w2a-error-trigger"]']) {
            const el = document.querySelector(sel);
            if (el) el.style.display = 'none';
          }
          return true;
        })()`,
      );
    }

    async function capture(page, name) {
      await hideControls(page.sessionId);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await sleep(250);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
      const buffer = Buffer.from(data, 'base64');
      const file = join(outDir, `${name}.png`);
      writeFileSync(file, buffer);
      screenshots.push({ name, file, sha256: createHash('sha256').update(buffer).digest('hex') });
    }

    async function openApp({ url, env, initScript, width, height }) {
      const page = await openPage({ url, env, initScript, width, height });
      await assertLocaleSetup(page.sessionId, `app[${env.id}]`, env);
      await waitForValue(page.sessionId, '!!window.__hrFirstShell', { label: 'first shell record' });
      return page;
    }

    // --- S1: saved English in a Chinese environment --------------------------
    {
      const page = await openApp({
        url: `${appUrl}orgs/demo-org/dashboard`,
        env: DEV_ENVIRONMENTS.zh,
        initScript: `${seedLocale('en')}\n${seedTheme('light')}`,
      });
      await waitForValue(page.sessionId, `document.querySelector('[role="navigation"][aria-label="Primary navigation"]')`, { label: 'en nav' });
      const snap = await snapshot(page.sessionId);
      check('S1 saved-en html.lang', snap.lang, 'en');
      check('S1 saved-en first shell locale', snap.firstShell.locale, 'en');
      check('S1 saved-en first shell title', snap.firstShell.title, 'Home');
      check('S1 saved-en first shell nav[0]', snap.firstShell.navLabels[0], 'Home');
      check('S1 saved-en AppBar title', snap.appBarTitle, 'Home');
      check('S1 saved-en nav aria', snap.navAria, 'Primary navigation');
      check('S1 saved-en account label', snap.accountLabel, 'Account: You, Founder');
      await capture(page, 'en-dashboard-1440-light');
      await closePage(page);
    }

    // --- S2: saved zh-CN in an English environment (wide + narrow, light + dark)
    {
      const page = await openApp({
        url: `${appUrl}orgs/demo-org/dashboard`,
        env: DEV_ENVIRONMENTS.en,
        initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}`,
        width: 1440,
        height: 900,
      });
      await waitForValue(page.sessionId, `document.querySelector('[role="navigation"][aria-label="主导航"]')`, {
        label: 'zh nav',
      });
      const snap = await snapshot(page.sessionId);
      check('S2 saved-zh html.lang', snap.lang, 'zh-CN');
      check('S2 saved-zh first shell locale', snap.firstShell.locale, 'zh-CN');
      check('S2 saved-zh first shell title', snap.firstShell.title, '首页');
      check('S2 saved-zh first shell htmlLang', snap.firstShell.htmlLang, 'zh-CN');
      check('S2 saved-zh first shell nav', snap.firstShell.navLabels, [
        '首页', '会话', '任务', '作业', '待办', '智能体', '工时', '技能', '知识库', '产物', '审计', '梦境', '用量', '运行状况', '设置',
      ]);
      check('S2 saved-zh AppBar title', snap.appBarTitle, '首页');
      check('S2 saved-zh nav aria', snap.navAria, '主导航');
      check('S2 saved-zh account label', snap.accountLabel, '账户：你，创始人');
      checkIncludes('S2 saved-zh theme aria', snap.themeLabel, '主题');
      check('S2 saved-zh task href', snap.navLinks.find((l) => l.text === '任务')?.href, '/orgs/demo-org/tasks');
      await capture(page, 'zh-dashboard-1440-light');

      const geo1440 = await geometry(page.sessionId);
      check('S2 zh 1440 no document horizontal overflow', geo1440.docScrollWidth <= geo1440.vw + 1, true);
      check('S2 zh 1440 no nav link outside viewport', geo1440.outside, 0);
      const navFonts = await platformFontsForSelector(page.sessionId, 'nav[aria-label="主导航项"] a');
      notes.push({ zhNavFonts: navFonts.map((f) => f.familyName) });
      checkTruthy('S2 zh nav glyphs use a CJK-capable platform font', navFonts.some((f) => isCjkFamily(f.familyName)));

      // Narrow + dark.
      await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, page.sessionId);
      await evaluate(page.sessionId, `(() => { localStorage.setItem('happyranch.theme','dark'); document.documentElement.setAttribute('data-theme','dark'); return true; })()`);
      await sleep(300);
      const geo390 = await geometry(page.sessionId);
      check('S2 zh 390 dark applied', await evaluate(page.sessionId, `document.documentElement.getAttribute('data-theme')`), 'dark');
      check('S2 zh 390 no document horizontal overflow', geo390.docScrollWidth <= geo390.vw + 1, true);
      check('S2 zh 390 no nav link outside viewport', geo390.outside, 0);
      await capture(page, 'zh-dashboard-390-dark');
      await closePage(page);
    }

    // --- S3: unset preference in a Chinese environment stays English (preview)
    {
      const page = await openApp({
        url: `${appUrl}orgs/demo-org/dashboard`,
        env: DEV_ENVIRONMENTS.zh,
        initScript: `${clearLocale()}\n${seedTheme('light')}`,
      });
      await sleep(1200);
      const snap = await snapshot(page.sessionId);
      check('S3 preview-unset html.lang', snap.lang, 'en');
      check('S3 preview-unset first shell locale', snap.firstShell.locale, 'en');
      check('S3 preview-unset title', snap.firstShell.title, 'Home');
      await closePage(page);
    }

    // --- S4: root loading copy in zh-CN, then the populated shell ------------
    {
      syntheticOrgDelayMs = 3000;
      const page = await openPage({
        url: appUrl,
        env: DEV_ENVIRONMENTS.en,
        initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}`,
      });
      const loading = await evaluate(page.sessionId, `document.body.textContent || ''`);
      checkIncludes('S4 root loading copy (zh-CN)', loading, '加载中…');
      await capture(page, 'zh-root-loading-1440-light');
      syntheticOrgDelayMs = 0;
      await waitForValue(page.sessionId, `!!document.querySelector('[role="navigation"][aria-label="主导航"]')`, { label: 'post-redirect zh nav' });
      await closePage(page);
    }

    // --- S5: no-org + NotFound, zh-CN and en --------------------------------
    {
      syntheticNoOrg = true;
      const zhPage = await openApp({
        url: `${appUrl}totally-unknown`,
        env: DEV_ENVIRONMENTS.en,
        initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}`,
      });
      await sleep(800);
      const zhSnap = await snapshot(zhPage.sessionId);
      checkIncludes('S5 zh not-found copy', zhSnap.bodyText, '未找到。');
      check('S5 zh not-found home link', zhSnap.notFoundLink, '返回首页');
      checkIncludes('S5 zh no-org copy', zhSnap.bodyText, '无组织');
      await capture(zhPage, 'zh-not-found-1440-light');
      await closePage(zhPage);

      const enPage = await openApp({
        url: `${appUrl}totally-unknown`,
        env: DEV_ENVIRONMENTS.zh,
        initScript: `${seedLocale('en')}\n${seedTheme('light')}`,
      });
      await sleep(800);
      const enSnap = await snapshot(enPage.sessionId);
      checkIncludes('S5 en not-found copy', enSnap.bodyText, 'Not found.');
      check('S5 en not-found home link', enSnap.notFoundLink, 'Go home');
      checkIncludes('S5 en no-org copy', enSnap.bodyText, 'No org');
      syntheticNoOrg = false;
      await closePage(enPage);
    }

    // --- S6: help drawer zh-CN with a non-default tab, preserved across switch
    {
      const page = await openApp({
        url: `${appUrl}orgs/demo-org/dashboard`,
        env: DEV_ENVIRONMENTS.en,
        initScript: `${seedLocale('en')}\n${seedTheme('light')}`,
      });
      await waitForValue(page.sessionId, `document.querySelector('[role="navigation"][aria-label="Primary navigation"]')`, { label: 'en shell' });
      await evaluate(page.sessionId, `(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: '?' })); return true; })()`);
      await waitForValue(page.sessionId, `!!document.querySelector('[id$="-trigger-tasks"]')`, { label: 'help task tab' });
      // Move to the Tasks tab with a real pointer click on its Radix trigger id.
      await clickSelector(page.sessionId, '[id$="-trigger-tasks"]');
      await waitForValue(
        page.sessionId,
        `(() => { const t=[...document.querySelectorAll('[role="tab"]')].find(x=>x.getAttribute('data-state')==='active'); return t && t.textContent.trim()==='Tasks'; })()`,
        { label: 'tasks tab active' },
      );
      await evaluate(page.sessionId, `document.querySelector('[data-testid="w2a-set-zh"]').click()`);
      await waitForValue(page.sessionId, `document.querySelector('[role="tab"]') && document.querySelector('[role="tab"]').textContent.includes('全局')`, { label: 'zh help tabs' });
      const helpSnap = await snapshot(page.sessionId);
      checkTruthy('S6 zh help dialog open', helpSnap.dialogTexts.some((t) => t.includes('键盘快捷键')));
      checkTruthy('S6 zh help Translations', helpSnap.dialogTexts.some((t) => t.includes('关闭打开的任务抽屉或对话框')));
      const activeTab = await evaluate(
        page.sessionId,
        `(() => { const t=[...document.querySelectorAll('[role="tab"]')].find(x=>x.getAttribute('data-state')==='active'); return t ? t.textContent.trim() : null; })()`,
      );
      check('S6 preserved non-default tab across locale switch', activeTab, '任务');
      const helpGeo = await geometry(page.sessionId);
      notes.push({ helpDialogRect: helpGeo.dialogRect, helpViewport: { vw: helpGeo.vw, vh: helpGeo.vh } });
      check(
        'S6 help dialog stays inside the viewport',
        Boolean(helpGeo.dialogRect) &&
          helpGeo.dialogRect.top >= -1 &&
          helpGeo.dialogRect.bottom <= helpGeo.vh + 1 &&
          helpGeo.dialogRect.right <= helpGeo.vw + 1,
        true,
      );
      await capture(page, 'zh-help-tasks-tab-1440-light');
      await closePage(page);
    }

    // --- S7: AddOrgDialog zh-CN, typed slug preserved across switch ----------
    {
      const page = await openApp({
        url: `${appUrl}orgs/demo-org/dashboard`,
        env: DEV_ENVIRONMENTS.en,
        initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}`,
      });
      await waitForValue(page.sessionId, `document.querySelector('[aria-label="当前组织"]')`, { label: 'zh org trigger' });
      await clickSelector(page.sessionId, '[aria-label="当前组织"]');
      await waitForValue(page.sessionId, `!!document.querySelector('[role="option"]')`, { label: 'org options' });
      // Choose the last option — the "+ 添加组织…" item below the org list.
      await evaluate(
        page.sessionId,
        `(() => { const options=[...document.querySelectorAll('[role="option"]')]; options[options.length-1].click(); return true; })()`,
      );
      await waitForValue(page.sessionId, `!!document.querySelector('[role="dialog"]')`, { label: 'add-org dialog' });
      const before = await snapshot(page.sessionId);
      checkTruthy('S7 zh AddOrg dialog title', before.dialogTexts.some((t) => t.includes('新建组织')));
      const addOrgGeo = await geometry(page.sessionId);
      notes.push({ addOrgDialogRect: addOrgGeo.dialogRect, addOrgViewport: { vw: addOrgGeo.vw, vh: addOrgGeo.vh } });
      check(
        'S7 add-org dialog stays inside the viewport',
        Boolean(addOrgGeo.dialogRect) &&
          addOrgGeo.dialogRect.top >= -1 &&
          addOrgGeo.dialogRect.bottom <= addOrgGeo.vh + 1 &&
          addOrgGeo.dialogRect.right <= addOrgGeo.vw + 1,
        true,
      );
      await setInputValue(page.sessionId, '#org-slug', 'typed-slug');
      await capture(page, 'zh-add-org-1440-light');
      await evaluate(page.sessionId, `document.querySelector('[data-testid="w2a-set-en"]').click()`);
      await sleep(300);
      const after = await snapshot(page.sessionId);
      checkTruthy('S7 en AddOrg dialog title after switch', after.dialogTexts.some((t) => t.includes('New org')));
      const slugValue = await evaluate(page.sessionId, `document.querySelector('#org-slug') ? document.querySelector('#org-slug').value : null`);
      check('S7 typed slug preserved across locale switch', slugValue, 'typed-slug');
      await closePage(page);
    }

    // --- S8: ErrorBoundary localized fallback with raw stack -----------------
    {
      const page = await openApp({
        url: `${appUrl}orgs/demo-org/dashboard`,
        env: DEV_ENVIRONMENTS.en,
        initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}`,
      });
      await waitForValue(page.sessionId, `!!document.querySelector('[data-testid="w2a-error-trigger"]')`, { label: 'error trigger' });
      await clickSelector(page.sessionId, '[data-testid="w2a-error-trigger"]');
      await waitForValue(page.sessionId, `!!document.querySelector('pre')`, { label: 'error fallback' });
      const errorSnap = await snapshot(page.sessionId);
      checkIncludes('S8 zh error title', errorSnap.bodyText, '此页面出现问题。');
      checkIncludes('S8 zh error body', errorSnap.bodyText, '应用的其他部分仍可使用');
      const preText = await evaluate(page.sessionId, `document.querySelector('pre') ? document.querySelector('pre').textContent : null`);
      checkIncludes('S8 raw error detail preserved', preText, 'W2A evidence error detail');
      const errorGeo = await geometry(page.sessionId);
      check('S8 error fallback no horizontal overflow', errorGeo.docScrollWidth <= errorGeo.vw + 1, true);
      await capture(page, 'zh-error-boundary-1440-light');
      await closePage(page);
    }

    // --- S9: palette probe typed query preserved across switch ---------------
    {
      const page = await openApp({
        url: `${appUrl}orgs/demo-org/dashboard`,
        env: DEV_ENVIRONMENTS.en,
        initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}`,
      });
      await waitForValue(page.sessionId, `!!document.querySelector('[data-testid="w2a-open-palette"]')`, { label: 'palette control' });
      await clickSelector(page.sessionId, '[data-testid="w2a-open-palette"]');
      await waitForValue(page.sessionId, `!!document.querySelector('input[role="combobox"]')`, { label: 'palette input' });
      const placeholderBefore = await evaluate(page.sessionId, `document.querySelector('input[role="combobox"]').getAttribute('placeholder')`);
      check('S9 palette zh placeholder', placeholderBefore, '搜索会话、任务、智能体、组织、知识库…');
      await setInputValue(page.sessionId, 'input[role="combobox"]', '更新');
      await evaluate(page.sessionId, `document.querySelector('[data-testid="w2a-set-en"]').click()`);
      await sleep(300);
      const queryAfter = await evaluate(page.sessionId, `document.querySelector('input[role="combobox"]') ? document.querySelector('input[role="combobox"]').value : null`);
      check('S9 typed palette query preserved across switch', queryAfter, '更新');
      const placeholderAfter = await evaluate(page.sessionId, `document.querySelector('input[role="combobox"]').getAttribute('placeholder')`);
      check('S9 palette en placeholder after switch', placeholderAfter, 'Search threads, tasks, agents, orgs, KB…');
      await capture(page, 'en-palette-probe-1440-light');
      await closePage(page);
    }

    const failed = assertions.filter((assertion) => !assertion.ok);
    const receipt = {
      head,
      generatedAt: new Date().toISOString(),
      node: process.version,
      distDir,
      environments,
      notes,
      assertions,
      screenshots,
      passed: assertions.length - failed.length,
      failed: failed.length,
    };
    writeFileSync(join(outDir, 'receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`);
    console.log(`\n${receipt.passed}/${assertions.length} assertions passed; ${screenshots.length} screenshots in ${outDir}`);
    process.exitCode = failed.length ? 1 : 0;
  } catch (error) {
    console.error(`w2a shell evidence harness failed: ${error.stack || error.message}`);
    if (chromeStderr) console.error(`chrome stderr:\n${chromeStderr.slice(-2000)}`);
    process.exitCode = 1;
  } finally {
    try {
      if (cdp) await cdp.send('Browser.close');
    } catch {
      /* ignore */
    }
    try {
      if (cdp) cdp.close();
    } catch {
      /* ignore */
    }
    if (chrome && chrome.exitCode === null) chrome.kill('SIGKILL');
    appServer.server.close();
    try {
      rmSync(userDataDir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
}

main();
