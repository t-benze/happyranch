#!/usr/bin/env node
/**
 * W2c Settings ▸ Preferences browser-evidence harness (THR-118).
 *
 * Drives ONE isolated headless Chrome over the DevTools Protocol (no new
 * dependency: Node 24's built-in WebSocket + the installed Chrome) against two
 * REAL production SPA bundles served same-origin next to a synthetic
 * `/api/v1` stub whose every request is recorded in a server-side ledger:
 *
 *   --preview-dist  built with VITE_ENABLE_I18N_PREFERENCES=true (explicit
 *                   test activation of the closed W2c gate)
 *   --dist          the ordinary shipping build (gate closed)
 *
 * Cases (each recorded in receipt.json; exit code 1 if any required case fails):
 *   A  ordinary dist: /settings/preferences redirects to /settings/assistant,
 *      no panel, no sub-nav link, and the gated strings are absent from the
 *      shipped JS (positive control: present in the preview JS);
 *   B  preview dist with settings API ok | error | loading: real CDP
 *      mouse/keyboard switching en→zh-CN→en and zh-CN→en→zh-CN asserting
 *      heading, <html lang>, retained radio node identity, actual focus,
 *      unchanged pathname, localStorage, status copy and ZERO /api requests;
 *   C  Storage.prototype.setItem throws for the locale key: in-memory switch
 *      still applies and the localized failure status is shown;
 *   D  a second same-origin tab writes localStorage → first tab updates
 *      without reload;
 *   E  zh-CN glyph/layout: non-zero, unclipped labels + heading, no document
 *      horizontal overflow at 1440x900 and 390x844, CJK glyphs measured via
 *      canvas measureText vs a notdef (tofu) control — never document.fonts.check;
 *   F  causal negatives: the SAME predicates must return false for a forced
 *      wrong lang, a remounted (cloned) radio, and an in-window API PUT;
 *   G  PNG screenshots with sha256 manifest;
 *   H  (TASK-8791) Settings ▸ Organization: a Work Hours success banner that is
 *      ALREADY visible re-translates when a second tab switches the locale
 *      (en→zh-CN→en and zh-CN→en→zh-CN) — same banner/switch/input nodes,
 *      dirty value and focus kept, exactly one PUT, ZERO /api requests in each
 *      switch window. With --defect-dist (a preview build of the rejected
 *      head that stored the pre-translated string) the SAME banner predicate
 *      must fail: the causal negative for a pretranslated state string;
 *   I  (TASK-8791) Settings ▸ Executors: raw daemon diagnostics (register 422,
 *      profile-remove 409) whose bytes EQUAL the English product fallback stay
 *      byte-for-byte across both switches; an injected "translated" diagnostic
 *      fails the same raw predicate (causal negative).
 *
 * Build + run (from web/):
 *   VITE_ENABLE_I18N_PREFERENCES=true ./node_modules/.bin/vite build --outDir <tmp>/dist-w2c-preview
 *   ./node_modules/.bin/vite build --outDir <tmp>/dist-ordinary
 *   node scripts/w2c-preferences-browser-evidence.mjs \
 *     --preview-dist <tmp>/dist-w2c-preview --dist <tmp>/dist-ordinary \
 *     --out <evidence dir> --head <40-char-sha> [--chrome google-chrome] \
 *     [--defect-dist <preview build of the rejected head b8ee7294>]
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { extname, join, resolve } from 'node:path';

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 || index + 1 >= process.argv.length ? fallback : process.argv[index + 1];
}

const previewDist = arg('preview-dist') && resolve(arg('preview-dist'));
const ordinaryDist = arg('dist') && resolve(arg('dist'));
const defectDist = arg('defect-dist') && resolve(arg('defect-dist'));
const outDir = resolve(arg('out', '.w2c-preferences-evidence'));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', process.env.CHROME_BIN || 'google-chrome');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

const ORG = 'test-org';
const LOCALE_KEY = 'happyranch.ui.locale';
const RADIO = 'input[name="happyranch-ui-language"]';
const radioSel = (value) => `${RADIO}[value="${value}"]`;
const PANEL = '[data-testid="settings-preferences"]';
const STATUS = '[data-testid="settings-preferences-status"]';
const PREFS_PATH = `/orgs/${ORG}/settings/preferences`;
const ASSISTANT_PATH = `/orgs/${ORG}/settings/assistant`;
const GATED_STRINGS = ['settings-preferences', 'happyranch-ui-language'];
const ORG_PATH = `/orgs/${ORG}/settings/organization`;
const EXECUTORS_PATH = `/orgs/${ORG}/settings/executors`;
const WH_SAVED = {
  en: 'Saved ✓ — takes effect at the next scheduler pass (≈ within ~60s).',
  'zh-CN': '已保存 ✓ — 将在下一次调度器轮询时生效（≈ 约 60 秒内）。',
};
// Raw daemon diagnostics deliberately byte-equal to the ENGLISH product
// fallbacks: a string-lookup translation would wrongly localize them.
const RAW_REGISTER = 'Could not register this path.';
const RAW_REMOVE = 'Could not remove this profile.';
const FALLBACK_ZH = { register: '无法注册此路径。', remove: '无法移除此配置。' };

const COPY = {
  en: { heading: 'Preferences', durable: 'Saved in this browser.', failed: 'Could not save in this browser. The language applies to this session only.' },
  'zh-CN': { heading: '偏好设置', durable: '已保存在此浏览器中。', failed: '无法保存在此浏览器中。该语言仅在本次会话中生效。' },
};

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.ico': 'image/x-icon',
};

// ------------------------------------------------------------------ synthetic API
/** Mutable knobs + the authoritative request ledger (one sequential harness). */
const SYNTH = { settingsMode: 'ok', ledger: [], hung: [] };

const SETTINGS_OK = {
  system: {},
  org: {
    session_timeout_seconds: null,
    reviewer_agents: [],
    dreaming: { enabled: false, schedule: { time: '03:00', timezone: 'UTC' }, agents: { mode: 'all', include: [], exclude: [] } },
    threads: { enabled: true, default_turn_cap: 5, invocation_timeout_seconds: null },
    working_hours: {
      enabled: false,
      agents: { mode: 'all', include: [], exclude: [] },
      default: { mode: 'windowed', window: { start: '09:00', end: '17:00', timezone: 'UTC' }, interval: '2h', days: ['mon'], catch_up_on_startup: false },
      teams: {},
      overrides: {},
    },
  },
};
const PROFILE = { name: 'my-runner', adapter: 'claude', workspace_adapter_id: 'claude', command_adapter_id: null, present: true, path: '/usr/local/bin/my-runner-cli' };

function apiBody(pathname) {
  if (pathname === '/api/v1/auth/bootstrap') return { token: 'w2c-evidence-token' };
  if (pathname === '/api/v1/orgs') return { orgs: [{ slug: ORG, root: `/runtime/${ORG}` }], broken: [] };
  if (pathname === `/api/v1/orgs/${ORG}/settings`) return SETTINGS_OK;
  if (pathname === `/api/v1/orgs/${ORG}/agents`) return { agents: [] };
  if (pathname === '/api/v1/executor-binaries') return { entries: [{ kind: 'claude', path: null, valid: false }] };
  if (pathname === '/api/v1/executors/runtime/profiles') return { profiles: [PROFILE] };
  if (pathname === '/api/v1/runtime/adapters') return [];
  if (pathname.endsWith('/status') && pathname.includes('assistant')) return { state: 'idle', available: false };
  return {};
}

function startServer(root, label) {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        if (url.pathname.startsWith('/api/')) {
          SYNTH.ledger.push({ dist: label, method: request.method, path: url.pathname, t: Date.now() });
          if (url.pathname === `/api/v1/orgs/${ORG}/settings` && request.method === 'GET') {
            if (SYNTH.settingsMode === 'loading') {
              SYNTH.hung.push(response); // never respond: the query stays loading
              return;
            }
            if (SYNTH.settingsMode === 'error') {
              response.writeHead(500, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
              response.end(JSON.stringify({ detail: 'settings exploded: raw-ID-42' }));
              return;
            }
          }
          const json = (status, body) => {
            response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
            response.end(JSON.stringify(body));
          };
          if (url.pathname === `/api/v1/orgs/${ORG}/settings/org` && request.method === 'PUT') {
            // Stateless echo: the snapshot with the requested working_hours.enabled.
            let raw = '';
            request.on('data', (c) => { raw += c; });
            request.on('end', () => {
              let patch = {};
              try { patch = JSON.parse(raw || '{}'); } catch { /* keep {} */ }
              const enabled = patch?.working_hours?.enabled ?? SETTINGS_OK.org.working_hours.enabled;
              json(200, { ...SETTINGS_OK, org: { ...SETTINGS_OK.org, working_hours: { ...SETTINGS_OK.org.working_hours, enabled } } });
            });
            return;
          }
          if (url.pathname === '/api/v1/executor-binaries/register' && request.method === 'POST') {
            json(422, { detail: RAW_REGISTER });
            return;
          }
          if (url.pathname === `/api/v1/executors/runtime/profiles/${PROFILE.name}` && request.method === 'DELETE') {
            json(409, { detail: RAW_REMOVE });
            return;
          }
          if (url.pathname.endsWith('/events') || url.pathname.includes('/stream')) {
            // Long-lived SSE: keep open so the client never reconnect-loops.
            response.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
            SYNTH.hung.push(response);
            return;
          }
          response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
          response.end(JSON.stringify(apiBody(url.pathname)));
          return;
        }
        if (url.pathname === '/__w2c_blank') {
          // Non-app same-origin page for the second tab (case D): no SPA, no /api traffic.
          response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
          response.end('<!doctype html><title>w2c second tab</title>');
          return;
        }
        const relative = decodeURIComponent(url.pathname);
        let file = relative === '/' ? join(root, 'index.html') : join(root, relative);
        if (!existsSync(file) || statSync(file).isDirectory()) file = join(root, 'index.html');
        response.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
        response.end(readFileSync(file));
      } catch (error) {
        response.writeHead(500);
        response.end(String(error));
      }
    });
    server.on('upgrade', (request, socket) => {
      SYNTH.ledger.push({ dist: label, method: 'WS-UPGRADE', path: new URL(request.url, 'http://x').pathname, t: Date.now() });
      socket.destroy();
    });
    server.on('error', rejectPromise);
    server.listen(0, '127.0.0.1', () => resolvePromise({ server, url: `http://127.0.0.1:${server.address().port}` }));
  });
}

// ------------------------------------------------------------------ dist inspection
function distJsFiles(dir) {
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

function distFingerprint(dir) {
  const html = readFileSync(join(dir, 'index.html'), 'utf8');
  const mainRel = (html.match(/<script[^>]+src="([^"]+\.js)"/) || [])[1];
  const mainPath = mainRel ? join(dir, mainRel.replace(/^\//, '')) : null;
  const files = distJsFiles(dir);
  const joined = files.map((f) => readFileSync(f, 'utf8')).join('\n');
  return {
    dir,
    indexHtmlSha256: sha256(readFileSync(join(dir, 'index.html'))),
    mainJs: mainRel,
    mainJsSha256: mainPath && existsSync(mainPath) ? sha256(readFileSync(mainPath)) : null,
    jsFileCount: files.length,
    gatedStrings: Object.fromEntries(GATED_STRINGS.map((s) => [s, joined.includes(s)])),
  };
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

// ------------------------------------------------------------------ init scripts
const seedLocale = (locale) => `try { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); } catch (e) {}`;
const seedTheme = (theme) => `try { localStorage.setItem('happyranch.theme', ${JSON.stringify(theme)}); } catch (e) {}`;
const FAIL_LOCALE_WRITES = `(() => {
  const original = Storage.prototype.setItem;
  Storage.prototype.setItem = function (key, value) {
    if (key === ${JSON.stringify(LOCALE_KEY)}) throw new DOMException('w2c evidence: locale write refused', 'SecurityError');
    return original.call(this, key, value);
  };
  window.__hrSetItemOverride = true;
})();`;

// ------------------------------------------------------------------ shared predicates
// The positive cases and the causal negatives (F) run these SAME expressions.
const PRED = {
  lang: (expected) => `document.documentElement.lang === ${JSON.stringify(expected)}`,
  identity: (key, selector) => `(() => {
    const ref = window.__hrRefs && window.__hrRefs[${JSON.stringify(key)}];
    const tagged = ref ? ref.deref() : null;
    const current = document.querySelector(${JSON.stringify(selector)});
    return Boolean(tagged && current && tagged === current && tagged.isConnected && tagged.__hrTag === ${JSON.stringify(key)});
  })()`,
  focused: (key) => `(() => { const ref = window.__hrRefs && window.__hrRefs[${JSON.stringify(key)}]; return Boolean(ref && ref.deref() && document.activeElement === ref.deref()); })()`,
};
const zeroApiPredicate = (delta) => delta.length === 0;

// ------------------------------------------------------------------ main
async function main() {
  if (!previewDist || !existsSync(join(previewDist, 'index.html'))) throw new Error('missing --preview-dist <dir> (gate-enabled build)');
  if (!ordinaryDist || !existsSync(join(ordinaryDist, 'index.html'))) throw new Error('missing --dist <dir> (ordinary build)');
  mkdirSync(outDir, { recursive: true });

  const cases = [];
  const screenshots = [];
  const notes = [];
  let current = null;

  function beginCase(id, title) {
    current = { id, title, checks: [], ledgerFrom: SYNTH.ledger.length, requests: [], pass: false };
    cases.push(current);
    console.log(`\n=== ${id}: ${title}`);
  }
  function endCase() {
    current.requests = SYNTH.ledger.slice(current.ledgerFrom);
    current.pass = current.checks.length > 0 && current.checks.every((c) => c.ok);
    delete current.ledgerFrom;
    console.log(`--- ${current.id} ${current.pass ? 'PASS' : 'FAIL'}`);
  }
  function check(name, actual, expected) {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    current.checks.push({ name, actual, expected, ok });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : ` — actual=${JSON.stringify(actual)} expected=${JSON.stringify(expected)}`}`);
    return ok;
  }
  /** Causal negative: the predicate MUST be false after the injected fault. */
  function negative(name, predicateValue, controlValue) {
    const ok = controlValue === true && predicateValue === false;
    current.checks.push({ name, controlBeforeFault: controlValue, predicateAfterFault: predicateValue, ok, verdict: ok ? 'negative control detected' : 'NOT detected' });
    console.log(`${ok ? 'PASS' : 'FAIL'} ${name} — ${ok ? 'negative control detected' : `control=${controlValue} after=${predicateValue}`}`);
  }

  const fpPreview = distFingerprint(previewDist);
  const fpOrdinary = distFingerprint(ordinaryDist);
  const preview = await startServer(previewDist, 'preview');
  const ordinary = await startServer(ordinaryDist, 'ordinary');
  if (defectDist && !existsSync(join(defectDist, 'index.html'))) throw new Error('--defect-dist has no index.html');
  const fpDefect = defectDist ? distFingerprint(defectDist) : null;
  const defect = defectDist ? await startServer(defectDist, 'defect') : null;

  const userDataDir = `/tmp/w2c-${process.pid}`; // short: Chrome's singleton socket path limit
  rmSync(userDataDir, { recursive: true, force: true });
  mkdirSync(userDataDir, { recursive: true });
  let chrome;
  let chromeStderr = '';
  let cdp;
  let chromeVersion = null;
  try {
    chrome = spawn(chromeBin, [
      '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${userDataDir}`,
      '--no-sandbox', '--disable-setuid-sandbox', '--no-first-run', '--no-default-browser-check', '--disable-gpu',
      '--disable-dev-shm-usage', '--disable-extensions', '--disable-background-networking', '--hide-scrollbars',
      '--disable-crash-reporter', '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
      '--disable-backgrounding-occluded-windows', 'about:blank',
    ], {
      stdio: ['ignore', 'ignore', 'pipe'],
      env: { ...process.env, HOME: userDataDir, TMPDIR: userDataDir, TMP: userDataDir, TEMP: userDataDir },
    });
    chrome.stderr.on('data', (chunk) => { chromeStderr += chunk.toString(); });
    const devtools = await waitForDevTools(userDataDir);
    chromeVersion = (await (await fetch(`http://127.0.0.1:${devtools.port}/json/version`)).json()).Browser;
    cdp = new CDP(`ws://127.0.0.1:${devtools.port}${devtools.path}`);
    await cdp.ready;

    // ---------------------------------------------------------- page helpers
    async function openPage({ url, init = '', width = 1440, height = 900 }) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      for (const domain of ['Page', 'Runtime', 'DOM', 'CSS']) await cdp.send(`${domain}.enable`, {}, sessionId);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, sessionId);
      if (init.trim()) await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: init }, sessionId);
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
    async function waitForValue(page, expression, label, timeout = 15000) {
      const deadline = Date.now() + timeout;
      let last;
      while (Date.now() < deadline) {
        try { last = await evaluate(page, expression); } catch (error) { last = `error: ${error.message}`; }
        if (last === true || (last && typeof last !== 'string') || (typeof last === 'string' && !last.startsWith('error:') && last)) return last;
        await sleep(100);
      }
      throw new Error(`timeout waiting for ${label}; last=${JSON.stringify(last)}`);
    }

    async function mouseClick(page, selector) {
      const { root } = await cdp.send('DOM.getDocument', { depth: -1 }, page.sessionId);
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector }, page.sessionId);
      if (!nodeId) throw new Error(`mouseClick: not found ${selector}`);
      await cdp.send('DOM.scrollIntoViewIfNeeded', { nodeId }, page.sessionId);
      const { model } = await cdp.send('DOM.getBoxModel', { nodeId }, page.sessionId);
      const x = (model.border[0] + model.border[4]) / 2;
      const y = (model.border[1] + model.border[5]) / 2;
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 }, page.sessionId);
    }
    /** Real mouse click on the first `selector` match whose trimmed text is `text`. */
    async function clickByText(page, selector, text) {
      const box = await evaluate(page, `(() => { const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find((e) => e.textContent.trim() === ${JSON.stringify(text)}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      if (!box) throw new Error(`clickByText: no ${selector} with text ${text}`);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box.x, y: box.y }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 }, page.sessionId);
    }
    const KEYS = {
      Space: { key: ' ', code: 'Space', windowsVirtualKeyCode: 32, text: ' ' },
      Tab: { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9 },
      ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', windowsVirtualKeyCode: 38 },
      ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', windowsVirtualKeyCode: 40 },
    };
    async function pressKey(page, name) {
      const { text, ...k } = KEYS[name];
      await cdp.send('Input.dispatchKeyEvent', { type: text ? 'keyDown' : 'rawKeyDown', ...k, ...(text ? { text } : {}) }, page.sessionId);
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...k }, page.sessionId);
    }
    /** Real Tab presses until a language radio holds focus (the checked one). */
    async function tabToRadio(page) {
      await evaluate(page, `(() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); return true; })()`);
      for (let i = 0; i < 60; i += 1) {
        await pressKey(page, 'Tab');
        if (await evaluate(page, `Boolean(document.activeElement && document.activeElement.matches(${JSON.stringify(RADIO)}))`)) return i + 1;
      }
      throw new Error('tabToRadio: never reached a language radio');
    }

    /** Tag the panel, heading and both radios with a JS property + WeakRef. */
    const TAG_NODES = `(() => {
      window.__hrRefs = {};
      const tag = (key, el) => { if (!el) return false; el.__hrTag = key; window.__hrRefs[key] = new WeakRef(el); return true; };
      const panel = document.querySelector(${JSON.stringify(PANEL)});
      return [
        tag('panel', panel),
        tag('heading', panel && panel.closest('div.p-6') && panel.closest('div.p-6').querySelector('h2')),
        tag('radio-en', document.querySelector(${JSON.stringify(radioSel('en'))})),
        tag('radio-zh-CN', document.querySelector(${JSON.stringify(radioSel('zh-CN'))})),
      ].every(Boolean);
    })()`;

    const STATE = `(() => {
      const panel = document.querySelector(${JSON.stringify(PANEL)});
      const container = panel && panel.closest('div.p-6');
      const h2 = container && container.querySelector('h2');
      const status = document.querySelector(${JSON.stringify(STATUS)});
      const checked = document.querySelector(${JSON.stringify(RADIO + ':checked')});
      let stored = null; try { stored = localStorage.getItem(${JSON.stringify(LOCALE_KEY)}); } catch (e) { stored = 'error'; }
      return {
        pathname: location.pathname,
        lang: document.documentElement.lang,
        heading: h2 ? h2.textContent.trim() : null,
        status: status ? status.textContent.trim() : null,
        checked: checked ? checked.value : null,
        stored,
        loadMarker: window.__hrLoadMarker || null,
      };
    })()`;

    /** Run one real-input switch and assert the whole predicate set. */
    async function assertSwitch(page, { label, target, input, expectStatus, expectStored }) {
      const radioKey = `radio-${target}`;
      const from = SYNTH.ledger.length;
      if (input === 'mouse') await mouseClick(page, radioSel(target));
      else if (input === 'space') {
        await evaluate(page, `(() => { document.querySelector(${JSON.stringify(radioSel(target))}).focus({ focusVisible: true }); return true; })()`);
        await pressKey(page, 'Space');
      } else if (input === 'arrowDown' || input === 'arrowUp') await pressKey(page, input === 'arrowDown' ? 'ArrowDown' : 'ArrowUp');
      await waitForValue(page, `document.documentElement.lang === ${JSON.stringify(target)} && (document.querySelector(${JSON.stringify(STATUS)}) || {}).textContent === ${JSON.stringify(expectStatus)}`, `${label} settle`, 5000).catch(() => null);
      await sleep(400); // quiescence window: any request caused by the switch lands in the ledger
      const delta = SYNTH.ledger.slice(from);
      const state = await evaluate(page, STATE);
      check(`${label} heading`, state.heading, COPY[target].heading);
      check(`${label} <html lang>`, await evaluate(page, PRED.lang(target)), true);
      check(`${label} radio node identity retained`, await evaluate(page, PRED.identity(radioKey, radioSel(target))), true);
      check(`${label} panel node identity retained`, await evaluate(page, PRED.identity('panel', PANEL)), true);
      check(`${label} activeElement is that radio`, await evaluate(page, PRED.focused(radioKey)), true);
      check(`${label} radio checked`, state.checked, target);
      check(`${label} pathname unchanged`, state.pathname, PREFS_PATH);
      check(`${label} localStorage`, state.stored, expectStored === undefined ? target : expectStored);
      check(`${label} status copy`, state.status, expectStatus);
      check(`${label} zero /api requests (any method) in switch window`, zeroApiPredicate(delta), true);
      current.switchWindows = [...(current.switchWindows || []), { label, input, delta }];
      return state;
    }

    async function capture(page, name, meta) {
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await sleep(250);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
      const buffer = Buffer.from(data, 'base64');
      const file = join(outDir, `${name}.png`);
      writeFileSync(file, buffer);
      screenshots.push({ name, file, sha256: sha256(buffer), bytes: buffer.length, ...meta });
    }
    async function setViewport(page, width, height) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, page.sessionId);
      await sleep(300);
    }
    async function setTheme(page, theme) {
      await evaluate(page, `(() => { localStorage.setItem('happyranch.theme', ${JSON.stringify(theme)}); document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)}); return true; })()`);
      await sleep(200);
    }

    async function openPrefs({ locale, mode, init = '', width, height, theme = 'light' }) {
      SYNTH.settingsMode = mode;
      const page = await openPage({ url: `${preview.url}${PREFS_PATH}`, init: `${seedLocale(locale)}\n${seedTheme(theme)}\n${init}`, width, height });
      await waitForValue(page, `Boolean(document.querySelector(${JSON.stringify(radioSel('zh-CN'))}))`, `preferences panel (${mode})`);
      await evaluate(page, `(() => { window.__hrLoadMarker = 'load-' + Math.random().toString(36).slice(2); return true; })()`);
      if (mode === 'error') await waitForValue(page, `document.body.textContent.includes('raw-ID-42') || document.body.textContent.includes('API 500')`, 'settings error state', 8000).catch(() => null);
      await sleep(300);
      return page;
    }

    const GEOMETRY = `(() => {
      const r = (el) => { const b = el.getBoundingClientRect(); return { w: b.width, h: b.height, left: b.left, right: b.right }; };
      const panel = document.querySelector(${JSON.stringify(PANEL)});
      const container = panel.closest('div.p-6');
      const heading = container.querySelector('h2');
      const labels = [...panel.querySelectorAll('label')];
      const zhSpan = panel.querySelector('span[lang="zh-CN"]');
      const clipped = (el) => el.scrollWidth > el.clientWidth + 1;
      const ancestorsClip = (el) => { let n = el.parentElement; const out = []; const b = el.getBoundingClientRect();
        while (n && n !== document.body) { const cs = getComputedStyle(n); const nb = n.getBoundingClientRect();
          if (cs.overflowX !== 'visible' && (b.right > nb.right + 1 || b.left < nb.left - 1)) out.push(n.tagName + '.' + n.className.toString().slice(0, 40));
          n = n.parentElement; } return out; };
      return {
        vw: window.innerWidth,
        docScrollWidth: document.documentElement.scrollWidth,
        mainScroller: (() => { const m = panel.closest('main'); return m ? { clientWidth: m.clientWidth, scrollWidth: m.scrollWidth, horizontalScroll: m.scrollWidth > m.clientWidth + 1 } : null; })(),
        zhLabelLines: (() => { const sp = panel.querySelector('span[lang="zh-CN"]'); const lh = parseFloat(getComputedStyle(sp).lineHeight) || 20; return Math.round(sp.getBoundingClientRect().height / lh); })(),
        headingLines: (() => { const lh = parseFloat(getComputedStyle(heading).lineHeight) || 28; return Math.round(heading.getBoundingClientRect().height / lh); })(),
        panel: { ...r(panel), scrollWidth: panel.scrollWidth, clientWidth: panel.clientWidth, clipped: clipped(panel) },
        heading: { text: heading.textContent, ...r(heading), clipped: clipped(heading), clippedBy: ancestorsClip(heading) },
        zhSpan: { text: zhSpan.textContent, ...r(zhSpan), clippedBy: ancestorsClip(zhSpan) },
        labels: labels.map((l) => ({ text: l.textContent.trim(), ...r(l), scrollWidth: l.scrollWidth, clientWidth: l.clientWidth, clipped: clipped(l), clippedBy: ancestorsClip(l) })),
      };
    })()`;
    const GLYPHS = `(() => {
      const heading = document.querySelector(${JSON.stringify(PANEL)}).closest('div.p-6').querySelector('h2');
      const cs = getComputedStyle(heading);
      const font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
      const ctx = document.createElement('canvas').getContext('2d');
      ctx.font = font;
      const cjk = ctx.measureText('偏好设置').width;
      const tofu = ctx.measureText('\\uE000\\uE001\\uE002\\uE003').width; // PUA: no glyph → notdef boxes
      const pixelsDiffer = (() => { const c = document.createElement('canvas'); c.width = 200; c.height = 60; const g = c.getContext('2d'); g.font = font;
        const draw = (s) => { g.clearRect(0, 0, 200, 60); g.fillText(s, 4, 40); return Array.from(g.getImageData(0, 0, 200, 60).data).join(','); };
        return draw('偏好设置') !== draw('\\uE000\\uE001\\uE002\\uE003'); })();
      return { font, cjkWidth: cjk, tofuWidth: tofu, widthDiffers: Math.abs(cjk - tofu) > 0.5, pixelsDiffer };
    })()`;
    async function platformFonts(page, selector) {
      const { root } = await cdp.send('DOM.getDocument', { depth: -1 }, page.sessionId);
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector }, page.sessionId);
      if (!nodeId) return [];
      return ((await cdp.send('CSS.getPlatformFontsForNode', { nodeId }, page.sessionId)).fonts || []).map((f) => f.familyName);
    }

    // ============================================================ A: ordinary dist
    beginCase('A', 'ordinary dist keeps the Preferences gate closed');
    {
      for (const s of GATED_STRINGS) {
        check(`A ordinary JS lacks "${s}"`, fpOrdinary.gatedStrings[s], false);
        check(`A preview JS contains "${s}" (positive control)`, fpPreview.gatedStrings[s], true);
      }
      SYNTH.settingsMode = 'ok';
      const page = await openPage({ url: `${ordinary.url}${PREFS_PATH}`, init: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page, `location.pathname === ${JSON.stringify(ASSISTANT_PATH)}`, 'redirect to assistant').catch(() => null);
      await sleep(800);
      const result = await evaluate(page, `(() => ({
        pathname: location.pathname,
        panel: Boolean(document.querySelector(${JSON.stringify(PANEL)})),
        radios: document.querySelectorAll(${JSON.stringify(RADIO)}).length,
        prefsLinks: [...document.querySelectorAll('a')].filter((a) => (a.getAttribute('href') || '').endsWith('/settings/preferences') || a.textContent.trim() === 'Preferences').length,
        subNavLinks: [...document.querySelectorAll('aside a')].map((a) => a.textContent.trim()),
      }))()`);
      check('A pathname ends at assistant', result.pathname, ASSISTANT_PATH);
      check('A no preferences panel', result.panel, false);
      check('A no language radios', result.radios, 0);
      check('A sub-nav has no Preferences link', result.prefsLinks, 0);
      current.observed = result;
      await capture(page, 'ordinary-preferences-redirect-1440-light-en', { dist: 'ordinary', viewport: '1440x900', theme: 'light', locale: 'en', state: 'redirected-to-assistant' });
      await closePage(page);
    }
    endCase();

    // ============================================================ B: switching per settings mode
    for (const mode of ['ok', 'error', 'loading']) {
      beginCase(`B-${mode}-en-first`, `preview dist, settings API ${mode}: en→zh-CN (mouse) →en (Space)`);
      {
        const page = await openPrefs({ locale: 'en', mode });
        const initial = await evaluate(page, STATE);
        check('initial lang en', initial.lang, 'en');
        check('initial heading', initial.heading, COPY.en.heading);
        const subnav = await evaluate(page, `[...document.querySelectorAll('aside a')].some((a) => a.getAttribute('href') === ${JSON.stringify(PREFS_PATH)})`);
        check('sub-nav lists Preferences', subnav, true);
        check('all nodes tagged', await evaluate(page, TAG_NODES), true);
        await assertSwitch(page, { label: 'en→zh-CN', target: 'zh-CN', input: 'mouse', expectStatus: COPY['zh-CN'].durable });
        await assertSwitch(page, { label: 'zh-CN→en', target: 'en', input: 'space', expectStatus: COPY.en.durable });
        check('no reload across both switches', (await evaluate(page, STATE)).loadMarker, initial.loadMarker);
        await closePage(page);
      }
      endCase();

      beginCase(`B-${mode}-zh-first`, `preview dist, settings API ${mode}: zh-CN→en (Space) →zh-CN (ArrowDown)`);
      {
        const page = await openPrefs({ locale: 'zh-CN', mode });
        const initial = await evaluate(page, STATE);
        check('initial lang zh-CN', initial.lang, 'zh-CN');
        check('initial heading', initial.heading, COPY['zh-CN'].heading);
        check('initial zh-CN radio checked', initial.checked, 'zh-CN');
        check('all nodes tagged', await evaluate(page, TAG_NODES), true);
        await assertSwitch(page, { label: 'zh-CN→en', target: 'en', input: 'space', expectStatus: COPY.en.durable });
        await assertSwitch(page, { label: 'en→zh-CN', target: 'zh-CN', input: 'arrowDown', expectStatus: COPY['zh-CN'].durable });
        check('no reload across both switches', (await evaluate(page, STATE)).loadMarker, initial.loadMarker);
        if (mode === 'error') {
          const errorText = await evaluate(page, `document.body.textContent`);
          current.errorStateText = errorText.includes('无法加载设置') ? 'localized load error visible' : 'load error not in DOM (preferences route is outside the settings gate)';
          await capture(page, 'zh-preferences-settings-api-error-1440-light', { dist: 'preview', viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'settings-api-error-500' });
        }
        if (mode === 'loading') await capture(page, 'zh-preferences-settings-api-loading-1440-light', { dist: 'preview', viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'settings-api-loading' });
        await closePage(page);
      }
      endCase();
    }

    // ============================================================ C: storage write failure
    beginCase('C', 'Storage.setItem throws for the locale key: in-memory switch + localized failure status');
    {
      const page = await openPrefs({ locale: 'en', mode: 'ok', init: FAIL_LOCALE_WRITES });
      check('setItem override installed before app', await evaluate(page, 'window.__hrSetItemOverride === true'), true);
      check('all nodes tagged', await evaluate(page, TAG_NODES), true);
      const storedBefore = (await evaluate(page, STATE)).stored;
      await assertSwitch(page, { label: 'en→zh-CN (write refused)', target: 'zh-CN', input: 'mouse', expectStatus: COPY['zh-CN'].failed, expectStored: storedBefore });
      check('stored value unchanged by refused write', (await evaluate(page, STATE)).stored, storedBefore);
      const danger = await evaluate(page, `document.querySelector(${JSON.stringify(STATUS)}).className.includes('text-feedback-danger')`);
      check('failure status uses danger tone', danger, true);
      await capture(page, 'zh-preferences-storage-failed-1440-light', { dist: 'preview', viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'storage-write-failed' });
      await assertSwitch(page, { label: 'zh-CN→en (write refused)', target: 'en', input: 'space', expectStatus: COPY.en.failed, expectStored: storedBefore });
      await closePage(page);
    }
    endCase();

    // ============================================================ D: cross-tab storage event
    beginCase('D', 'second same-origin tab writes the locale → first tab updates without reload');
    {
      const first = await openPrefs({ locale: 'en', mode: 'ok' });
      check('all nodes tagged', await evaluate(first, TAG_NODES), true);
      const before = await evaluate(first, STATE);
      const from = SYNTH.ledger.length;
      const second = await openPage({ url: `${preview.url}/__w2c_blank` });
      const wrote = await evaluate(second, `(() => { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, 'zh-CN'); return localStorage.getItem(${JSON.stringify(LOCALE_KEY)}); })()`);
      check('second tab wrote zh-CN', wrote, 'zh-CN');
      await cdp.send('Page.bringToFront', {}, first.sessionId);
      await waitForValue(first, PRED.lang('zh-CN'), 'first tab follows storage event', 5000).catch(() => null);
      await sleep(400);
      const after = await evaluate(first, STATE);
      check('first tab heading', after.heading, COPY['zh-CN'].heading);
      check('first tab <html lang>', await evaluate(first, PRED.lang('zh-CN')), true);
      check('first tab radio checked', after.checked, 'zh-CN');
      check('first tab not reloaded (load marker kept)', after.loadMarker, before.loadMarker);
      check('first tab panel node identity retained', await evaluate(first, PRED.identity('panel', PANEL)), true);
      check('first tab pathname unchanged', after.pathname, PREFS_PATH);
      const delta = SYNTH.ledger.slice(from);
      check('zero /api requests during cross-tab update', zeroApiPredicate(delta), true);
      await closePage(second);
      await closePage(first);
    }
    endCase();

    // ============================================================ E + G: glyphs, layout, screenshots
    beginCase('E', 'zh-CN glyph rendering and no clipping/overflow at 1440x900 and 390x844');
    {
      const page = await openPrefs({ locale: 'zh-CN', mode: 'ok' });
      for (const [w, h] of [[1440, 900], [390, 844]]) {
        await setViewport(page, w, h);
        const geo = await evaluate(page, GEOMETRY);
        const vp = `${w}x${h}`;
        current[`geometry_${vp}`] = geo;
        check(`${vp} heading text`, geo.heading.text, COPY['zh-CN'].heading);
        check(`${vp} heading non-zero width`, geo.heading.w > 0 && geo.heading.h > 0, true);
        check(`${vp} heading not clipped`, !geo.heading.clipped && geo.heading.clippedBy.length === 0, true);
        check(`${vp} 简体中文 span non-zero width`, geo.zhSpan.text === '简体中文' && geo.zhSpan.w > 0, true);
        check(`${vp} 简体中文 span not clipped by an ancestor`, geo.zhSpan.clippedBy.length === 0, true);
        check(`${vp} panel scrollWidth<=clientWidth`, !geo.panel.clipped, true);
        check(`${vp} every label unclipped`, geo.labels.every((l) => !l.clipped && l.clippedBy.length === 0 && l.w > 0), true);
        check(`${vp} no document horizontal overflow`, geo.docScrollWidth <= geo.vw + 1, true);
        check(`${vp} settings <main> scroller has no horizontal overflow`, geo.mainScroller && !geo.mainScroller.horizontalScroll, true);
        // Wide: the endonym stays on one line. Narrow: the desktop-only shell
        // leaves a ~134px Settings column at 390, so CJK may legitimately wrap
        // between characters; the required narrow predicate is containment
        // (panel/label/document checks above), and the line count is recorded.
        if (w === 1440) check(`${vp} 简体中文 label renders on one line`, geo.zhLabelLines, 1);
        else current[`zhLabelLines_${vp}`] = geo.zhLabelLines;
      }
      await setViewport(page, 1440, 900);
      const glyphs = await evaluate(page, GLYPHS);
      current.glyphs = glyphs;
      check('canvas: 偏好设置 width differs from notdef (tofu) control', glyphs.widthDiffers, true);
      check('canvas: 偏好设置 pixels differ from notdef (tofu) control', glyphs.pixelsDiffer, true);
      const fonts = await platformFonts(page, `${PANEL} span[lang="zh-CN"]`);
      current.zhSpanPlatformFonts = fonts;
      check('简体中文 span rendered with a CJK platform font', fonts.some((f) => /CJK|Han|Hei|Song|WenQuanYi|SC|PingFang|YaHei/i.test(f)), true);
      await closePage(page);

      // Screenshot matrix: locale × viewport × theme.
      for (const locale of ['en', 'zh-CN']) {
        for (const theme of ['light', 'dark']) {
          const shot = await openPrefs({ locale, mode: 'ok', theme });
          await setTheme(shot, theme);
          for (const [w, h] of [[1440, 900], [390, 844]]) {
            await setViewport(shot, w, h);
            check(`screenshot ${locale} ${theme} ${w} data-theme applied`, await evaluate(shot, `document.documentElement.getAttribute('data-theme')`), theme);
            await capture(shot, `${locale === 'en' ? 'en' : 'zh'}-preferences-${w}-${theme}`, { dist: 'preview', viewport: `${w}x${h}`, theme, locale, state: 'settings-ok' });
          }
          await closePage(shot);
        }
      }
    }
    endCase();

    // ============================================================ focus ring (keyboard)
    beginCase('G-focus', 'keyboard Tab reaches the radio and the focus ring is visible');
    {
      const page = await openPrefs({ locale: 'zh-CN', mode: 'ok' });
      check('all nodes tagged', await evaluate(page, TAG_NODES), true);
      const tabs = await tabToRadio(page);
      current.tabPresses = tabs;
      const ring = await evaluate(page, `(() => { const el = document.activeElement; const cs = getComputedStyle(el);
        return { value: el.value, focusVisible: el.matches(':focus-visible'), boxShadow: cs.boxShadow }; })()`);
      current.focusRing = ring;
      check('Tab lands on checked zh-CN radio', ring.value, 'zh-CN');
      check('radio matches :focus-visible', ring.focusVisible, true);
      check('focus ring box-shadow painted', Boolean(ring.boxShadow) && ring.boxShadow !== 'none', true);
      await capture(page, 'zh-preferences-keyboard-focus-1440-light', { dist: 'preview', viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'keyboard-focus-visible' });
      await assertSwitch(page, { label: 'zh-CN→en (ArrowUp from Tab focus)', target: 'en', input: 'arrowUp', expectStatus: COPY.en.durable });
      await closePage(page);
    }
    endCase();

    // ============================================================ F: causal negatives
    beginCase('F', 'causal negatives: the same predicates reject injected faults');
    {
      const page = await openPrefs({ locale: 'en', mode: 'ok' });
      check('all nodes tagged', await evaluate(page, TAG_NODES), true);
      await mouseClick(page, radioSel('zh-CN'));
      await waitForValue(page, PRED.lang('zh-CN'), 'switch before faults');
      await sleep(300);

      // (1) wrong locale
      const langControl = await evaluate(page, PRED.lang('zh-CN'));
      await evaluate(page, `(() => { document.documentElement.lang = 'en'; return true; })()`);
      negative('F1 forced <html lang="en"> fails the lang predicate', await evaluate(page, PRED.lang('zh-CN')), langControl);
      await evaluate(page, `(() => { document.documentElement.lang = 'zh-CN'; return true; })()`);

      // (3) API write inside a switch window (before the remount fault so the radio is live)
      const from = SYNTH.ledger.length;
      const zeroControl = zeroApiPredicate(SYNTH.ledger.slice(from));
      await mouseClick(page, radioSel('en'));
      await evaluate(page, `fetch('/api/v1/orgs/${ORG}/settings/org', { method: 'PUT', body: '{}' }).then((r) => r.status).catch((e) => String(e))`);
      await sleep(400);
      const delta = SYNTH.ledger.slice(from);
      current.apiFaultDelta = delta;
      negative('F3 in-window PUT /settings/org fails the zero-request predicate', zeroApiPredicate(delta), zeroControl);

      // (2) remount: replace the radio with a clone
      const idControl = await evaluate(page, PRED.identity('radio-zh-CN', radioSel('zh-CN')));
      await evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(radioSel('zh-CN'))}); el.replaceWith(el.cloneNode(true)); return true; })()`);
      negative('F2 cloned radio fails the identity predicate', await evaluate(page, PRED.identity('radio-zh-CN', radioSel('zh-CN'))), idControl);
      await closePage(page);
    }
    endCase();

    // ============================================================ H: Work Hours banner relocalizes (TASK-8791)
    /** The single visible Work Hours success banner (either language). */
    const BANNER_EXPR = `[...document.querySelectorAll('[role="status"]')].filter((el) => el.textContent === ${JSON.stringify(WH_SAVED.en)} || el.textContent === ${JSON.stringify(WH_SAVED['zh-CN'])})`;
    const WH_SWITCH = 'button[role="switch"][aria-labelledby="work-hours-switch-label"]';
    const TIMEOUT_INPUT = 'input[type="number"]';
    const PRED_H = {
      banner: (locale) => `(() => { const b = ${BANNER_EXPR}; return b.length === 1 && b[0].textContent === ${JSON.stringify(WH_SAVED[locale])}; })()`,
      bannerIdentity: `(() => { const b = ${BANNER_EXPR}; const ref = window.__hrRefs && window.__hrRefs.banner; return Boolean(ref && b.length === 1 && ref.deref() === b[0] && b[0].__hrTag === 'banner'); })()`,
    };
    const TAG_ORG_NODES = `(() => {
      window.__hrRefs = {};
      const tag = (key, el) => { if (!el) return false; el.__hrTag = key; window.__hrRefs[key] = new WeakRef(el); return true; };
      const b = ${BANNER_EXPR};
      return [tag('banner', b.length === 1 ? b[0] : null), tag('wh-switch', document.querySelector(${JSON.stringify(WH_SWITCH)})), tag('timeout', document.querySelector(${JSON.stringify(TIMEOUT_INPUT)}))].every(Boolean);
    })()`;

    /** A second same-origin tab writes the locale → storage event in `page`. */
    async function crossTabSwitch(page, server, locale) {
      const other = await openPage({ url: `${server.url}/__w2c_blank` });
      await evaluate(other, `(() => { localStorage.setItem(${JSON.stringify(LOCALE_KEY)}, ${JSON.stringify(locale)}); return true; })()`);
      await closePage(other);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await waitForValue(page, PRED.lang(locale), `storage-event switch to ${locale}`, 5000).catch(() => null);
    }

    /** Open Organization, enable Work Hours via a real click → banner; then type a dirty value. */
    async function openOrgWithBanner(server, locale, theme = 'light', { width = 1440, height = 900 } = {}) {
      SYNTH.settingsMode = 'ok';
      const page = await openPage({ url: `${server.url}${ORG_PATH}`, init: `${seedLocale(locale)}\n${seedTheme(theme)}`, width, height });
      await waitForValue(page, `Boolean(document.querySelector(${JSON.stringify(WH_SWITCH)}))`, 'organization panel');
      await evaluate(page, `(() => { window.__hrLoadMarker = 'load-' + Math.random().toString(36).slice(2); return true; })()`);
      const putsBefore = SYNTH.ledger.filter((r) => r.method === 'PUT').length;
      await mouseClick(page, WH_SWITCH);
      await waitForValue(page, PRED_H.banner(locale), `banner visible in ${locale}`, 8000).catch(() => null);
      await sleep(300);
      // A dirty field AFTER the save (the save response resyncs the form).
      await evaluate(page, `(() => { document.querySelector(${JSON.stringify(TIMEOUT_INPUT)}).focus(); return true; })()`);
      await cdp.send('Input.insertText', { text: '120' }, page.sessionId);
      await sleep(200);
      return { page, putsBefore };
    }

    const ORG_STATE = `(() => { const i = document.querySelector(${JSON.stringify(TIMEOUT_INPUT)}); return { pathname: location.pathname, lang: document.documentElement.lang, value: i ? i.value : null, loadMarker: window.__hrLoadMarker || null, activeIsTimeout: document.activeElement === (window.__hrRefs && window.__hrRefs.timeout && window.__hrRefs.timeout.deref()) }; })()`;

    async function runBannerCase(server, first, second, label) {
      const { page, putsBefore } = await openOrgWithBanner(server, first);
      check(`${label} banner visible in ${first} after a real save`, await evaluate(page, PRED_H.banner(first)), true);
      check(`${label} nodes tagged (banner, switch, dirty input)`, await evaluate(page, TAG_ORG_NODES), true);
      const initial = await evaluate(page, ORG_STATE);
      check(`${label} dirty value typed`, initial.value, '120');
      for (const target of [second, first]) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, server, target);
        await waitForValue(page, PRED_H.banner(target), `banner in ${target}`, 3000).catch(() => null);
        await sleep(400);
        const delta = SYNTH.ledger.slice(from);
        const state = await evaluate(page, ORG_STATE);
        const step = `${label} →${target}`;
        check(`${step} <html lang>`, await evaluate(page, PRED.lang(target)), true);
        check(`${step} banner text is the ${target} copy`, await evaluate(page, PRED_H.banner(target)), true);
        check(`${step} banner node identity retained`, await evaluate(page, PRED_H.bannerIdentity), true);
        check(`${step} work-hours switch identity retained`, await evaluate(page, PRED.identity('wh-switch', WH_SWITCH)), true);
        check(`${step} dirty input identity retained`, await evaluate(page, PRED.identity('timeout', TIMEOUT_INPUT)), true);
        check(`${step} dirty value retained`, state.value, '120');
        check(`${step} focus retained on the dirty input`, state.activeIsTimeout, true);
        check(`${step} pathname unchanged`, state.pathname, ORG_PATH);
        check(`${step} no reload`, state.loadMarker, initial.loadMarker);
        check(`${step} zero /api requests (any method) in switch window`, zeroApiPredicate(delta), true);
        current.switchWindows = [...(current.switchWindows || []), { label: step, input: 'storage-event', delta }];
      }
      const puts = SYNTH.ledger.filter((r) => r.method === 'PUT').length - putsBefore;
      check(`${label} exactly one PUT (no resave on switch)`, puts, 1);
      return page;
    }

    beginCase('H-en-first', 'Organization: visible Work Hours banner en→zh-CN→en via storage event, no resave/remount/request');
    await closePage(await runBannerCase(preview, 'en', 'zh-CN', 'H'));
    endCase();

    beginCase('H-zh-first', 'Organization: visible Work Hours banner zh-CN→en→zh-CN via storage event, no resave/remount/request');
    await closePage(await runBannerCase(preview, 'zh-CN', 'en', 'H'));
    endCase();

    if (defect) {
      beginCase('H-defect', 'causal negative: the rejected head (pre-translated banner string) FAILS the same banner predicate');
      {
        const fixedPage = (await openOrgWithBanner(preview, 'en')).page;
        await crossTabSwitch(fixedPage, preview, 'zh-CN');
        await sleep(400);
        const control = await evaluate(fixedPage, PRED_H.banner('zh-CN'));
        await closePage(fixedPage);
        const { page } = await openOrgWithBanner(defect, 'en');
        check('defect build: banner visible in en', await evaluate(page, PRED_H.banner('en')), true);
        await crossTabSwitch(page, defect, 'zh-CN');
        await sleep(600);
        check('defect build: <html lang> did switch (the switch itself works)', await evaluate(page, PRED.lang('zh-CN')), true);
        const observed = await evaluate(page, `(${BANNER_EXPR}).map((el) => el.textContent)`);
        current.defectBannerText = observed;
        negative('H-defect stale pre-translated banner fails the zh-CN banner predicate', await evaluate(page, PRED_H.banner('zh-CN')), control);
        await evaluate(page, `(() => { const b = ${BANNER_EXPR}; if (b[0]) b[0].scrollIntoView({ block: 'center' }); return true; })()`);
        await capture(page, 'defect-b8ee7294-zh-org-banner-stale-1440-light', { dist: 'defect', viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: 'rejected-head-stale-english-banner (negative control)' });
        await closePage(page);
      }
      endCase();
    } else {
      notes.push({ hDefect: 'SKIPPED: --defect-dist not supplied' });
    }

    // ============================================================ I: raw executor diagnostics stay verbatim (TASK-8791)
    beginCase('I', 'Executors: raw daemon diagnostics byte-equal to the English fallback stay verbatim across both switches');
    {
      SYNTH.settingsMode = 'ok';
      const page = await openPage({ url: `${preview.url}${EXECUTORS_PATH}`, init: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page, `Boolean(document.querySelector('[data-testid="binary-row-claude"]')) && Boolean(document.querySelector('[data-testid="profile-row-my-runner"]'))`, 'executors panel');
      // register → 422 raw
      await evaluate(page, `(() => { document.querySelector('[data-testid="binary-manual-claude"]').open = true; return true; })()`);
      await evaluate(page, `(() => { document.getElementById('binary-path-claude').focus(); return true; })()`);
      await cdp.send('Input.insertText', { text: '/opt/claude' }, page.sessionId);
      await clickByText(page, '[data-testid="binary-row-claude"] button', 'Register');
      await waitForValue(page, `Boolean(document.querySelector('[data-testid="binary-register-error-claude"]'))`, 'register raw error', 5000).catch(() => null);
      // profile remove → 409 raw
      await mouseClick(page, '[data-testid="profile-remove-my-runner"]');
      await waitForValue(page, `Boolean(document.querySelector('[data-testid="profile-confirm-remove-my-runner"]'))`, 'remove armed');
      await mouseClick(page, '[data-testid="profile-confirm-remove-my-runner"]');
      await waitForValue(page, `Boolean(document.querySelector('[data-testid="profile-remove-error-my-runner"]'))`, 'remove raw error', 5000).catch(() => null);
      const RAW = (sel, raw) => `(() => { const el = document.querySelector(${JSON.stringify(sel)}); return Boolean(el) && el.textContent === ${JSON.stringify(raw)}; })()`;
      const REG = '[data-testid="binary-register-error-claude"]';
      const REM = '[data-testid="profile-remove-error-my-runner"]';
      check('I register raw diagnostic shown verbatim (en)', await evaluate(page, RAW(REG, RAW_REGISTER)), true);
      check('I remove raw diagnostic shown verbatim (en)', await evaluate(page, RAW(REM, RAW_REMOVE)), true);
      await evaluate(page, `(() => { window.__hrRefs = {}; for (const [k, s] of [['reg', ${JSON.stringify(REG)}], ['rem', ${JSON.stringify(REM)}], ['path', '#binary-path-claude']]) { const el = document.querySelector(s); el.__hrTag = k; window.__hrRefs[k] = new WeakRef(el); } return true; })()`);
      for (const target of ['zh-CN', 'en', 'zh-CN']) {
        const from = SYNTH.ledger.length;
        await crossTabSwitch(page, preview, target);
        await sleep(400);
        const delta = SYNTH.ledger.slice(from);
        check(`I →${target} <html lang>`, await evaluate(page, PRED.lang(target)), true);
        check(`I →${target} register diagnostic byte-for-byte`, await evaluate(page, RAW(REG, RAW_REGISTER)), true);
        check(`I →${target} remove diagnostic byte-for-byte`, await evaluate(page, RAW(REM, RAW_REMOVE)), true);
        check(`I →${target} diagnostic nodes retained`, await evaluate(page, `${PRED.identity('reg', REG)} && ${PRED.identity('rem', REM)} && ${PRED.identity('path', '#binary-path-claude')}`), true);
        check(`I →${target} typed path retained`, await evaluate(page, `document.getElementById('binary-path-claude').value`), '/opt/claude');
        check(`I →${target} zero /api requests in switch window`, zeroApiPredicate(delta), true);
      }
      check('I product chrome did localize (control)', await evaluate(page, `document.querySelector('[data-testid="binary-manual-claude"] summary').textContent.includes('高级')`), true);
      for (const [sel, name] of [[REG, 'register'], [REM, 'remove']]) {
        await evaluate(page, `(() => { document.querySelector(${JSON.stringify(sel)}).scrollIntoView({ block: 'center' }); return true; })()`);
        await sleep(200);
        await capture(page, `zh-executors-raw-${name}-diagnostic-1440-light`, { dist: 'preview', viewport: '1440x900', theme: 'light', locale: 'zh-CN', state: `raw-daemon-${name}-diagnostic-verbatim-after-switch` });
      }
      // Causal negative: an accidentally translated diagnostic fails the raw predicate.
      const control = await evaluate(page, RAW(REG, RAW_REGISTER));
      await evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(REG)}); el.lastChild.textContent = ${JSON.stringify(FALLBACK_ZH.register)}; return true; })()`);
      negative('I-neg translated register diagnostic fails the raw predicate', await evaluate(page, RAW(REG, RAW_REGISTER)), control);
      const control2 = await evaluate(page, RAW(REM, RAW_REMOVE));
      await evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(REM)}); el.lastChild.textContent = ${JSON.stringify(FALLBACK_ZH.remove)}; return true; })()`);
      negative('I-neg translated remove diagnostic fails the raw predicate', await evaluate(page, RAW(REM, RAW_REMOVE)), control2);
      await closePage(page);
    }
    endCase();

    // ============================================================ H screenshots: corrected already-visible banner
    beginCase('H-shots', 'banner produced in one locale, switched by storage event, captured in the other (light/dark × 1440/390)');
    {
      for (const [produced, shown] of [['en', 'zh-CN'], ['zh-CN', 'en']]) {
        for (const theme of ['light', 'dark']) {
          const { page } = await openOrgWithBanner(preview, produced, theme);
          await setTheme(page, theme);
          await crossTabSwitch(page, preview, shown);
          await waitForValue(page, PRED_H.banner(shown), 'banner relocalized', 3000).catch(() => null);
          for (const [w, h] of [[1440, 900], [390, 844]]) {
            await setViewport(page, w, h);
            await evaluate(page, `(() => { const b = ${BANNER_EXPR}; if (b[0]) b[0].scrollIntoView({ block: 'center' }); return true; })()`);
            await sleep(200);
            check(`shot ${produced}→${shown} ${theme} ${w} banner in ${shown}`, await evaluate(page, PRED_H.banner(shown)), true);
            const inView = await evaluate(page, `(() => { const b = (${BANNER_EXPR})[0]; if (!b) return false; const r = b.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && r.width > 0; })()`);
            check(`shot ${produced}→${shown} ${theme} ${w} banner in viewport`, inView, true);
            check(`shot ${produced}→${shown} ${theme} ${w} no document horizontal overflow`, await evaluate(page, 'document.documentElement.scrollWidth <= innerWidth + 1'), true);
            await capture(page, `${shown === 'en' ? 'en' : 'zh'}-org-workhours-banner-from-${produced === 'en' ? 'en' : 'zh'}-${w}-${theme}`, { dist: 'preview', viewport: `${w}x${h}`, theme, locale: shown, state: `work-hours-banner-produced-in-${produced}-relocalized` });
          }
          await closePage(page);
        }
      }
    }
    endCase();
  } finally {
    for (const response of SYNTH.hung) { try { response.destroy(); } catch { /* gone */ } }
    if (cdp) cdp.close();
    if (chrome && !chrome.killed) chrome.kill('SIGKILL');
    preview.server.closeAllConnections?.();
    ordinary.server.closeAllConnections?.();
    preview.server.close();
    ordinary.server.close();
    if (defect) { defect.server.closeAllConnections?.(); defect.server.close(); }
    rmSync(userDataDir, { recursive: true, force: true });
  }

  const failed = cases.filter((c) => !c.pass);
  const receipt = {
    head,
    generatedAt: new Date().toISOString(),
    node: process.version,
    chrome: { path: chromeBin, version: chromeVersion },
    dists: { preview: fpPreview, ordinary: fpOrdinary, defect: fpDefect },
    gatedStringsChecked: GATED_STRINGS,
    notes: [...notes, ...(chromeStderr.trim() ? [{ chromeStderrTail: chromeStderr.slice(-1500) }] : [])],
    summary: cases.map((c) => ({ id: c.id, title: c.title, pass: c.pass, checks: c.checks.length, failedChecks: c.checks.filter((x) => !x.ok).map((x) => x.name) })),
    cases,
    screenshots,
    ledger: SYNTH.ledger,
    passed: cases.length - failed.length,
    failed: failed.length,
  };
  writeFileSync(join(outDir, 'receipt.json'), `${JSON.stringify(receipt, null, 2)}\n`);
  console.log(`\n${receipt.passed}/${cases.length} cases passed; ${screenshots.length} screenshots in ${outDir}`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
