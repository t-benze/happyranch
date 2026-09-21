#!/usr/bin/env node
/**
 * Supported browser-evidence harness for the THR-118 W1 i18n foundation.
 *
 * It drives ONE isolated headless Chrome over the DevTools Protocol (no new
 * dependency: Node 24's built-in WebSocket + the already-installed Chrome) and
 * asserts observable behaviour against:
 *
 *   1. the built Storybook foundation story
 *      (`src/design-system/i18n/I18nFoundation.stories.tsx`) — bilingual first
 *      paint, glyphs, state preservation across a locale switch, real
 *      same-origin tab change/clear/no-echo, and storage failure; and
 *   2. the production SPA bundle (`web/dist`) served same-origin next to a
 *      synthetic `/api/v1` stub — the REAL `main.tsx -> App ->
 *      createBrowserRouter -> AppShell -> I18nProvider` startup, proving
 *      `<html lang>` and the first React text agree.
 *
 * Usage (after `npm run build` and `npm run build-storybook`):
 *
 *   node scripts/i18n-browser-evidence.mjs \
 *     --dist ./dist --storybook ./storybook-static \
 *     --out <evidence dir> --head <40-char-sha>
 *
 * Exit 0 only when every assertion passes; writes `<out>/receipt.json` plus
 * SHA-256-bound PNG screenshots.
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
const storybookDir = resolve(arg('storybook', join(webRoot, 'storybook-static')));
const outDir = resolve(arg('out', join(webRoot, '.i18n-browser-evidence')));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');
const keepUserDataDir = process.argv.includes('--keep-profile');

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
  '.xml': 'application/xml',
};

const sleep = (ms) => new Promise((resolvePromise) => setTimeout(resolvePromise, ms));

/**
 * Chrome's process-singleton socket has a short (~104 char) path limit, and the
 * runtime task TMPDIR is long. Prefer a short writable POSIX temp root for the
 * disposable browser profile.
 */
function pickProfileRoot() {
  const candidates = [];
  if (process.platform !== 'win32') candidates.push('/tmp');
  candidates.push(tmpdir());
  for (const base of candidates) {
    try {
      mkdirSync(base, { recursive: true });
      const probe = join(base, `.hr-write-probe-${process.pid}`);
      writeFileSync(probe, '');
      rmSync(probe, { force: true });
      return base;
    } catch {
      /* try the next candidate */
    }
  }
  throw new Error('no writable temp root for the Chrome profile');
}

function apiBody(pathname) {
  if (pathname === '/api/v1/auth/bootstrap') return { token: 'browser-evidence-token' };
  if (pathname === '/api/v1/orgs') return { orgs: [], broken: [] };
  if (pathname === '/api/v1/health/prereqs') {
    return [{ tool: 'claude', present: true, path: '/usr/bin/claude', hint: '' }];
  }
  return {};
}

function startServer({ root, api }) {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        if (api && url.pathname.startsWith('/api/')) {
          response.writeHead(200, {
            'content-type': 'application/json; charset=utf-8',
            'cache-control': 'no-store',
          });
          response.end(JSON.stringify(apiBody(url.pathname)));
          return;
        }
        const relative = decodeURIComponent(url.pathname);
        let file = relative === '/' || relative === '' ? join(root, 'index.html') : join(root, relative);
        if (!existsSync(file) || statSync(file).isDirectory()) {
          if (!api) {
            response.writeHead(404);
            response.end('not found');
            return;
          }
          file = join(root, 'index.html');
        }
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
      const text =
        typeof event.data === 'string' ? event.data : Buffer.from(event.data).toString('utf8');
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

  on(method, handler) {
    const list = this.handlers.get(method) || [];
    list.push(handler);
    this.handlers.set(method, list);
    return () => {
      const current = this.handlers.get(method) || [];
      this.handlers.set(
        method,
        current.filter((candidate) => candidate !== handler),
      );
    };
  }

  waitFor(method, { sessionId, timeout = 30000 } = {}) {
    return new Promise((resolvePromise, rejectPromise) => {
      const off = this.on(method, (message) => {
        if (sessionId && message.sessionId !== sessionId) return;
        clearTimeout(timer);
        off();
        resolvePromise(message.params);
      });
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

const WRITE_COUNTER = `
window.__hrWrites = 0;
try {
  const proto = window.Storage && window.Storage.prototype;
  if (proto && typeof proto.setItem === 'function') {
    const original = proto.setItem;
    proto.setItem = function (...args) {
      try { window.__hrWrites++; } catch (error) {}
      return original.apply(this, args);
    };
  }
} catch (error) {}
`;

function seedLocaleSource(locale) {
  return `${WRITE_COUNTER}\ntry { localStorage.setItem('happyranch.ui.locale', ${JSON.stringify(
    locale,
  )}); } catch (error) {}`;
}

function clearLocaleSource() {
  return `${WRITE_COUNTER}\ntry { localStorage.removeItem('happyranch.ui.locale'); } catch (error) {}`;
}

const STORAGE_FAILURE = `
try {
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    get() { throw new DOMException('storage disabled by isolated probe', 'SecurityError'); },
  });
} catch (error) {}
`;

const LANG_PROBE = `
window.__hrLangProbe = { langAtStart: null, firstText: null, firstTextLang: null };
try {
  window.__hrLangProbe.langAtStart = document.documentElement
    ? document.documentElement.getAttribute('lang')
    : null;
} catch (error) {}
(function () {
  const record = () => {
    const probe = window.__hrLangProbe;
    if (!probe || probe.firstText !== null) return;
    const root = document.getElementById('root');
    if (!root) return;
    const text = (root.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!text) return;
    probe.firstText = text.slice(0, 80);
    probe.firstTextLang = document.documentElement
      ? document.documentElement.getAttribute('lang')
      : null;
  };
  const start = () => {
    record();
    try {
      new MutationObserver(record).observe(document.documentElement, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
      });
    } catch (error) {}
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
`;

async function main() {
  for (const dir of [distDir, storybookDir]) {
    if (!existsSync(dir)) {
      throw new Error(`missing build directory ${dir}; run the web build first`);
    }
  }
  mkdirSync(outDir, { recursive: true });

  const assertions = [];
  const screenshots = [];
  const notes = [];

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
  const storyServer = await startServer({ root: storybookDir, api: false });

  const indexResponse = await fetch(`http://127.0.0.1:${storyServer.port}/index.json`);
  const indexJson = await indexResponse.json();
  const storyEntry = Object.values(indexJson.entries || {}).find(
    (entry) => entry.title === 'Design System/I18n Foundation',
  );
  if (!storyEntry) throw new Error('foundation story not found in Storybook index.json');
  const storyUrl = `http://127.0.0.1:${storyServer.port}/iframe.html?id=${storyEntry.id}&viewMode=story`;
  const appUrl = `http://127.0.0.1:${appServer.port}/`;
  notes.push({ storyUrl, appUrl, storyId: storyEntry.id });

  const userDataDir = join(pickProfileRoot(), `hr-i18n-${process.pid}-${Date.now()}`);
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
        // The host's Chrome SUID sandbox helper is not root-owned 4755 (a
        // container/CI property, not a product surface); this isolated local
        // test browser still runs with no network access to product services.
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--disable-extensions',
        '--disable-background-networking',
        '--hide-scrollbars',
        '--window-size=1280,900',
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
    notes.push({ chrome: version.Browser, chromeBin, protocolVersion: version['Protocol-Version'] });

    async function openPage({ url, locale, initScript }) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      await cdp.send('Page.enable', {}, sessionId);
      await cdp.send('Runtime.enable', {}, sessionId);
      await cdp.send('Network.enable', {}, sessionId);
      await cdp.send('DOM.enable', {}, sessionId);
      await cdp.send('CSS.enable', {}, sessionId);
      await cdp.send('Network.setBlockedURLs', {
        urls: ['https://fonts.googleapis.com/*', 'https://fonts.gstatic.com/*'],
      }, sessionId);
      if (locale) {
        try {
          await cdp.send('Emulation.setLocaleOverride', { locale }, sessionId);
        } catch (error) {
          notes.push({ localeOverrideFailed: `${locale}: ${error.message}` });
        }
      }
      const source = [initScript, LANG_PROBE].filter(Boolean).join('\n');
      if (source.trim()) {
        await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source }, sessionId);
      }
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
      const result = await cdp.send(
        'Runtime.evaluate',
        { expression, returnByValue: true, awaitPromise: true },
        sessionId,
      );
      if (result.exceptionDetails) {
        throw new Error(
          `evaluate failed: ${result.exceptionDetails.text} ${
            result.exceptionDetails.exception?.description || ''
          }`,
        );
      }
      return result.result.value;
    }

    async function waitForValue(sessionId, expression, { timeout = 30000, label } = {}) {
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

    const PROBE_SNAPSHOT = `(() => {
      const q = (selector) => document.querySelector(selector);
      const text = (selector) => {
        const el = q(selector);
        return el ? el.textContent.trim() : null;
      };
      return {
        lang: document.documentElement.getAttribute('lang'),
        locale: text('[data-testid="probe-locale"]'),
        heading: text('[data-testid="probe-heading"]'),
        persistence: text('[data-testid="probe-persistence"]'),
        count: text('[data-testid="probe-count"]'),
        renderedCount: text('[data-testid="probe-rendered-count"]'),
        glyphs: text('[data-testid="probe-glyphs"]'),
        formatted: text('[data-testid="probe-count-formatted"]'),
        draft: q('[data-testid="probe-draft"]') ? q('[data-testid="probe-draft"]').value : null,
        selection: q('[data-testid="probe-selection"]')
          ? q('[data-testid="probe-selection"]').value
          : null,
        open: q('[data-testid="probe-open"]') ? q('[data-testid="probe-open"]').open : null,
        instance: q('[data-testid="foundation-probe"]')
          ? q('[data-testid="foundation-probe"]').dataset.instance || null
          : null,
        stored: (() => { try { return localStorage.getItem('happyranch.ui.locale'); } catch (error) { return 'THREW'; } })(),
        writes: typeof window.__hrWrites === 'number' ? window.__hrWrites : null,
        langProbe: window.__hrLangProbe || null,
      };
    })()`;

    async function snapshot(sessionId) {
      return evaluate(sessionId, PROBE_SNAPSHOT);
    }

    /**
     * The platform fonts the renderer actually used for a node. This is real
     * glyph evidence (not a string assertion): a CJK family must be selected for
     * the Chinese copy, otherwise the capture would be tofu.
     */
    async function platformFontsForSelector(sessionId, selector) {
      const { root } = await cdp.send('DOM.getDocument', { depth: -1 }, sessionId);
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector }, sessionId);
      if (!nodeId) return [];
      const { fonts } = await cdp.send('CSS.getPlatformFontsForNode', { nodeId }, sessionId);
      return fonts || [];
    }

    async function click(sessionId, testId, label) {
      await waitForValue(
        sessionId,
        `!!document.querySelector('[data-testid="${testId}"]')`,
        { label: `${label} button` },
      );
      await evaluate(
        sessionId,
        `(() => { document.querySelector('[data-testid="${testId}"]').click(); return true; })()`,
      );
      await sleep(120);
    }

    async function capture(page, name) {
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await sleep(200);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
      const buffer = Buffer.from(data, 'base64');
      const file = join(outDir, `${name}.png`);
      writeFileSync(file, buffer);
      screenshots.push({ name, file, sha256: createHash('sha256').update(buffer).digest('hex') });
      return file;
    }

    async function openStory({ locale, initScript }) {
      const page = await openPage({ url: storyUrl, locale, initScript });
      await waitForValue(page.sessionId, `!!document.querySelector('[data-testid="foundation-probe"]')`, {
        label: 'foundation story probe',
      });
      return page;
    }

    // --- S1: saved explicit English in a Chinese environment -----------------
    {
      const page = await openStory({ locale: 'zh-CN', initScript: seedLocaleSource('en') });
      const snap = await snapshot(page.sessionId);
      check('S1 saved-en-in-zh-env html.lang', snap.lang, 'en');
      check('S1 resolved locale', snap.locale, 'locale=en source=saved');
      check('S1 English translated heading', snap.heading, 'Translated probe');
      check('S1 English plural singular form', snap.count, '1 item');
      check('S1 English persisted value', snap.stored, 'en');
      await capture(page, 'story-en-in-zh-env');
      await closePage(page);
    }

    // --- S2: saved Simplified Chinese ---------------------------------------
    {
      const page = await openStory({ locale: 'en-US', initScript: seedLocaleSource('zh-CN') });
      const snap = await snapshot(page.sessionId);
      check('S2 saved-zh html.lang', snap.lang, 'zh-CN');
      check('S2 resolved locale', snap.locale, 'locale=zh-CN source=saved');
      check('S2 Chinese translated heading', snap.heading, '已翻译探针');
      check('S2 Chinese plural form', snap.count, '1 个项目');
      check('S2 rendered React node text', snap.renderedCount, 'Ada 选择了 1 个文件');
      check('S2 Chinese glyph copy', snap.glyphs, '此界面尚未提供中文版本。');
      check('S2 explicit-locale grouped count', snap.formatted, '1,234,567');
      const glyphFonts = await platformFontsForSelector(page.sessionId, '[data-testid="probe-glyphs"]');
      notes.push({ glyphFonts });
      checkTruthy(
        'S2 Chinese glyphs rendered by a CJK-capable platform font',
        glyphFonts.some((font) =>
          /CJK|Han|Hei|Song|WenQuanYi|Noto Sans SC|PingFang|Hiragino/i.test(font.familyName || ''),
        ),
      );
      await capture(page, 'story-zh-cn-glyphs');
      await closePage(page);
    }

    // --- S3: unset preference stays English in preview -----------------------
    {
      const page = await openStory({ locale: 'zh-CN', initScript: clearLocaleSource() });
      const snap = await snapshot(page.sessionId);
      check('S3 preview-unset html.lang', snap.lang, 'en');
      check('S3 preview-unset locale source', snap.locale, 'locale=en source=default');
      check('S3 preview-unset heading', snap.heading, 'Translated probe');
      check('S3 preview-unset stored value', snap.stored, null);
      await capture(page, 'story-preview-unset-zh-env');
      await closePage(page);
    }

    // --- S4: switching preserves draft / selection / open state --------------
    {
      const page = await openStory({ locale: 'zh-CN', initScript: seedLocaleSource('en') });
      await click(page.sessionId, 'set-draft', 'edit draft');
      await click(page.sessionId, 'set-selection', 'select gamma');
      await evaluate(
        page.sessionId,
        `(() => { const details = document.querySelector('[data-testid="probe-open"]'); details.open = false; return details.open; })()`,
      );
      await evaluate(
        page.sessionId,
        `(() => { document.querySelector('[data-testid="foundation-probe"]').dataset.instance = 'kept'; return true; })()`,
      );
      const before = await snapshot(page.sessionId);
      check('S4 pre-switch draft', before.draft, 'edited draft (unsent)');
      check('S4 pre-switch selection', before.selection, 'gamma');
      check('S4 pre-switch open state', before.open, false);
      await click(page.sessionId, 'set-zh', 'set zh');
      await waitForValue(page.sessionId, `document.documentElement.lang === 'zh-CN'`, {
        label: 'zh switch',
      });
      const after = await snapshot(page.sessionId);
      check('S4 post-switch html.lang', after.lang, 'zh-CN');
      check('S4 post-switch heading Chinese', after.heading, '已翻译探针');
      check('S4 draft preserved across switch', after.draft, 'edited draft (unsent)');
      check('S4 selection preserved across switch', after.selection, 'gamma');
      check('S4 open state preserved across switch', after.open, false);
      check('S4 no remount (same DOM probe instance)', after.instance, 'kept');
      check('S4 durable persistence', after.persistence, 'persistence=durable');
      check('S4 stored zh-CN', after.stored, 'zh-CN');
      await capture(page, 'story-zh-switch-state-preserved');
      await click(page.sessionId, 'set-en', 'set en');
      await waitForValue(page.sessionId, `document.documentElement.lang === 'en'`, {
        label: 'en switch',
      });
      const back = await snapshot(page.sessionId);
      check('S4 back-to-en heading', back.heading, 'Translated probe');
      check('S4 state preserved on the way back', [back.draft, back.selection, back.open, back.instance], [
        'edited draft (unsent)',
        'gamma',
        false,
        'kept',
      ]);
      await closePage(page);
    }

    // --- S5: storage read/write failure stays usable in memory ---------------
    {
      const page = await openStory({ initScript: STORAGE_FAILURE });
      const initial = await snapshot(page.sessionId);
      check('S5 storage-failure falls back to English', initial.lang, 'en');
      check('S5 storage read is a guarded failure', initial.stored, 'THREW');
      await click(page.sessionId, 'set-zh', 'set zh');
      await waitForValue(page.sessionId, `document.documentElement.lang === 'zh-CN'`, {
        label: 'in-memory zh switch',
      });
      const after = await snapshot(page.sessionId);
      check('S5 in-memory switch works', after.heading, '已翻译探针');
      checkIncludes('S5 honest failed saving', after.persistence, 'persistence=failed');
      checkIncludes('S5 failed reason is unavailable', after.persistence, 'unavailable');
      await capture(page, 'story-storage-failure-zh');
      await closePage(page);
    }

    // --- S6: real same-origin tabs change/clear with no echo write -----------
    {
      const pageA = await openStory({ initScript: seedLocaleSource('en') });
      const pageB = await openStory({});
      const beforeA = await snapshot(pageA.sessionId);
      const writesBefore = beforeA.writes;

      await evaluate(
        pageB.sessionId,
        `(() => { localStorage.setItem('happyranch.ui.locale', 'zh-CN'); return localStorage.getItem('happyranch.ui.locale'); })()`,
      );
      await waitForValue(pageA.sessionId, `document.documentElement.lang === 'zh-CN'`, {
        label: 'tab A zh sync',
      });
      const syncedA = await snapshot(pageA.sessionId);
      check('S6 tab change applies', syncedA.heading, '已翻译探针');
      check('S6 external change leaves persistence idle', syncedA.persistence, 'persistence=idle');
      check('S6 no echo write', syncedA.writes, writesBefore);
      check('S6 stored value is the external one', syncedA.stored, 'zh-CN');
      await capture(pageA, 'story-tab-sync-zh');

      await evaluate(
        pageB.sessionId,
        `(() => { localStorage.removeItem('happyranch.ui.locale'); return localStorage.getItem('happyranch.ui.locale'); })()`,
      );
      await waitForValue(pageA.sessionId, `document.documentElement.lang === 'en'`, {
        label: 'tab A deletion sync',
      });
      const removedA = await snapshot(pageA.sessionId);
      check('S6 key deletion resolves to English', removedA.heading, 'Translated probe');
      check('S6 deletion keeps no echo write', removedA.writes, writesBefore);

      await evaluate(pageB.sessionId, `(() => { localStorage.setItem('happyranch.ui.locale', 'zh-CN'); return true; })()`);
      await waitForValue(pageA.sessionId, `document.documentElement.lang === 'zh-CN'`, {
        label: 'tab A re-sync',
      });
      await evaluate(pageB.sessionId, `(() => { localStorage.clear(); return true; })()`);
      await waitForValue(pageA.sessionId, `document.documentElement.lang === 'en'`, {
        label: 'tab A clear sync',
      });
      const clearedA = await snapshot(pageA.sessionId);
      check('S6 clear() resolves to English', clearedA.heading, 'Translated probe');
      check('S6 clear keeps no echo write', clearedA.writes, writesBefore);
      await closePage(pageA);
      await closePage(pageB);
    }

    // --- S7-S9: the REAL production startup (main.tsx -> App) ----------------
    async function openApp({ locale, initScript }) {
      const page = await openPage({ url: appUrl, locale, initScript });
      await waitForValue(
        page.sessionId,
        `!!(window.__hrLangProbe && window.__hrLangProbe.firstText !== null)`,
        { label: 'app first React text' },
      );
      return page;
    }

    {
      const page = await openApp({
        locale: 'en-US',
        initScript: seedLocaleSource('zh-CN'),
      });
      const snap = await snapshot(page.sessionId);
      check('S7 app saved-zh html.lang', snap.lang, 'zh-CN');
      check('S7 app first-text lang matches resolved locale', snap.langProbe.firstTextLang, 'zh-CN');
      check('S7 app lang probe ran before the document element existed', snap.langProbe.langAtStart, null);
      checkTruthy('S7 app first React text recorded', snap.langProbe.firstText);
      await capture(page, 'app-main-startup-zh-cn');
      await closePage(page);
    }

    {
      const page = await openApp({ locale: 'zh-CN', initScript: seedLocaleSource('en') });
      const snap = await snapshot(page.sessionId);
      check('S8 app saved-en-in-zh-env html.lang', snap.lang, 'en');
      check('S8 app first-text lang', snap.langProbe.firstTextLang, 'en');
      await capture(page, 'app-main-startup-en-in-zh-env');
      await closePage(page);
    }

    {
      const page = await openApp({ locale: 'zh-CN', initScript: clearLocaleSource() });
      const snap = await snapshot(page.sessionId);
      check('S9 app preview-unset-in-zh-env html.lang', snap.lang, 'en');
      check('S9 app first-text lang', snap.langProbe.firstTextLang, 'en');
      await capture(page, 'app-main-startup-preview-zh-env');
      await closePage(page);
    }

    const failed = assertions.filter((assertion) => !assertion.ok);
    const receipt = {
      head,
      generatedAt: new Date().toISOString(),
      node: process.version,
      distDir,
      storybookDir,
      notes,
      assertions,
      screenshots,
      passed: assertions.length - failed.length,
      failed: failed.length,
    };
    writeFileSync(join(outDir, 'receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`);
    console.log(
      `\n${receipt.passed}/${assertions.length} assertions passed; ${screenshots.length} screenshots in ${outDir}`,
    );
    process.exitCode = failed.length ? 1 : 0;
  } catch (error) {
    console.error(`browser evidence harness failed: ${error.stack || error.message}`);
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
    if (chrome && chrome.exitCode === null) {
      chrome.kill('SIGKILL');
    }
    appServer.server.close();
    storyServer.server.close();
    if (!keepUserDataDir) {
      try {
        rmSync(userDataDir, { recursive: true, force: true });
      } catch {
        /* ignore */
      }
    }
  }
}

main();
