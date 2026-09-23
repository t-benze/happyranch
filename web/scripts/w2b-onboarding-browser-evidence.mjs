#!/usr/bin/env node
/**
 * W2b onboarding browser-evidence harness (THR-118).
 *
 * Drives ONE isolated headless Chrome over the DevTools Protocol (no new
 * dependency: Node 24's built-in WebSocket + the already-installed Chrome)
 * against the REAL production SPA bundle (`web/dist`) served same-origin next
 * to a synthetic `/api/v1` stub, and then navigates the genuine `/onboarding`
 * route.
 *
 * There is deliberately NO evidence-only bundle instrumentation for W2b: the
 * harness drives the shipping build and uses the supported browser
 * `localStorage` preference + a same-origin `storage` event to switch locale
 * (there is no public language selector). It therefore proves the ordinary
 * bundle already satisfies the acceptance predicate.
 *
 * It asserts, in both locales:
 *   - first-run vs returning routing and the connect wrapper copy;
 *   - built-in and custom connect modes, the generated copy-paste prompt bytes
 *     preserved across en→zh-CN→en, and the localized conformance-step labels
 *     around the raw step ids;
 *   - create-form validation, a mapped daemon error re-translating without
 *     resubmission, an unknown raw diagnostic preserved byte-for-byte, and the
 *     success state;
 *   - the read-only broken-org list (raw slug/error verbatim) and the executor
 *     prereq panel (raw tool/path verbatim);
 *   - retained DOM identity, actual focus, phase/mode/value/raw bytes, and
 *     zero API requests or mutations across both directions of every S4-S6
 *     and S8-S10 locale switch;
 *   - PNG screenshots at 1440x900 and 390x844, light and dark.
 *
 * Usage:
 *   npm run build
 *   node scripts/w2b-onboarding-browser-evidence.mjs --dist ./dist \
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
const outDir = resolve(arg('out', join(webRoot, '.w2b-onboarding-evidence')));
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

/** Mutable synthetic-API knobs (single sequential harness, module scope fine). */
const SYNTH = {
  orgs: [],
  broken: [],
  prereqs: [],
  createOrg: { status: 200, body: { slug: 'created-org' } },
  minted: 0,
  createCalls: 0,
};

const RAW_ERROR = 'backend raw reason 0xDEADBEEF — org lock held';
const BROKEN_SLUG = 'broken-org';
const BROKEN_ERROR = 'daemon raw: EACCES open /runtime/broken-org 0xDEAD';

function apiBody(pathname, method) {
  if (pathname === '/api/v1/auth/bootstrap') return { token: 'w2b-evidence-token' };
  if (pathname === '/api/v1/orgs' && method === 'POST') {
    SYNTH.createCalls += 1;
    return SYNTH.createOrg.body;
  }
  if (pathname === '/api/v1/orgs') return { orgs: SYNTH.orgs, broken: SYNTH.broken };
  if (pathname === '/api/v1/health/prereqs') return { prereqs: SYNTH.prereqs };
  if (pathname === '/api/v1/auth/registration-token/runtime') {
    SYNTH.minted += 1;
    return { token: 'hr_tok_W2B_EVIDENCE', expires_at: Date.now() / 1000 + 1800 };
  }
  if (pathname === '/api/v1/runtime/custom-cli/status') {
    return {
      wrapper_destination: '/tmp/happyranch-daemon/adapters/my-cli-adapter',
      operation_id: null,
      profile_state: null,
      reason: null,
      state: null,
      retry_eligible: false,
    };
  }
  return {};
}

function startServer({ root, api }) {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://127.0.0.1');
        if (api && url.pathname.startsWith('/api/')) {
          const isCreateOrg = url.pathname === '/api/v1/orgs' && request.method === 'POST';
          const status = isCreateOrg ? SYNTH.createOrg.status : 200;
          response.writeHead(status, {
            'content-type': 'application/json; charset=utf-8',
            'cache-control': 'no-store',
          });
          response.end(JSON.stringify(apiBody(url.pathname, request.method)));
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

  on(method, handler) {
    const list = this.handlers.get(method) || [];
    list.push(handler);
    this.handlers.set(method, list);
    return () => {
      const current = this.handlers.get(method) || [];
      this.handlers.set(method, current.filter((candidate) => candidate !== handler));
    };
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
      const probe = join(base, `.hr-w2b-probe-${process.pid}`);
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
  catch (error) { failures.push('define languages'); }
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
function seedTheme(theme) {
  return `try { localStorage.setItem('happyranch.theme', ${JSON.stringify(theme)}); } catch (error) {}`;
}

async function main() {
  if (!existsSync(distDir)) throw new Error(`missing build directory ${distDir}; run the web build first`);
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

  function finalizeReceipt() {
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
  }

  const appServer = await startServer({ root: distDir, api: true });
  const appUrl = `http://127.0.0.1:${appServer.port}`;
  notes.push({ appUrl });

  const userDataDir = join(pickProfileRoot(), `hr-w2b-${process.pid}-${Date.now()}`);
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

    const networkRequests = [];
    cdp.on('Network.requestWillBeSent', (message) => {
      const request = message.params && message.params.request;
      if (request) networkRequests.push({ method: request.method, url: request.url });
    });

    async function openPage({ url, env, initScript, width = 1440, height = 900 }) {
      const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
      await cdp.send('Page.enable', {}, sessionId);
      await cdp.send('Runtime.enable', {}, sessionId);
      await cdp.send('Network.enable', {}, sessionId);
      await cdp.send('DOM.enable', {}, sessionId);
      await cdp.send('CSS.enable', {}, sessionId);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 600 }, sessionId);
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

    async function waitForValue(sessionId, expression, { timeout = 15000, label } = {}) {
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

    async function clickByText(sessionId, text) {
      const clicked = await evaluate(
        sessionId,
        `(() => {
          const els = [...document.querySelectorAll('button, a')];
          const el = els.find((e) => (e.textContent || '').trim().includes(${JSON.stringify(text)}));
          if (!el) return false;
          el.click();
          return true;
        })()`,
      );
      if (!clicked) throw new Error(`clickByText: not found ${text}`);
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

    async function selectOption(sessionId, selector, value) {
      return evaluate(
        sessionId,
        `(() => {
          const el = document.querySelector(${JSON.stringify(selector)});
          if (!el) return null;
          const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
          setter.call(el, ${JSON.stringify(value)});
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return el.value;
        })()`,
      );
    }

    const SNAP = `(() => {
      const heading = [...document.querySelectorAll('h1, h2')].map((h) => (h.textContent || '').trim());
      const main = document.querySelector('main') || document.body;
      const pre = document.querySelector('pre');
      const slug = document.querySelector('#onboarding-slug');
      const adapterName = document.querySelector('#adapter-name');
      const controls = [...document.querySelectorAll('button, a')].map((b) => ({
        text: (b.textContent || '').trim(),
        disabled: b.disabled === true,
      }));
      return {
        lang: document.documentElement.getAttribute('lang'),
        htmlTheme: document.documentElement.getAttribute('data-theme'),
        pathname: location.pathname,
        headings: heading,
        bodyText: (main.textContent || ''),
        preText: pre ? pre.textContent : null,
        slugValue: slug ? slug.value : null,
        adapterNameValue: adapterName ? adapterName.value : null,
        controls,
      };
    })()`;

    async function snapshot(sessionId) {
      return evaluate(sessionId, SNAP);
    }

    const GEOMETRY = `(() => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const pre = document.querySelector('pre');
      const preRect = pre ? pre.getBoundingClientRect() : null;
      return {
        vw, vh,
        docScrollWidth: document.documentElement.scrollWidth,
        bodyScrollWidth: document.body.scrollWidth,
        preOverflowRight: preRect ? preRect.right > vw + 1 : false,
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
      return /CJK|Han|Hei|Song|WenQuanYi|Noto Sans SC|Noto Serif SC|PingFang|Hiragino|YaHei|Microsoft YaHei/i.test(name || '');
    }

    async function capture(page, name) {
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await sleep(250);
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' }, page.sessionId);
      const buffer = Buffer.from(data, 'base64');
      const file = join(outDir, `${name}.png`);
      writeFileSync(file, buffer);
      screenshots.push({ name, file, sha256: createHash('sha256').update(buffer).digest('hex') });
    }

    async function assertLocaleSetup(sessionId, label, env) {
      const setup = await evaluate(sessionId, 'window.__hrLocaleSetup || null');
      const ok = Boolean(setup && setup.ok);
      assertions.push({ name: `${label} navigator language asserted before app modules`, actual: setup ? setup.actual : null, expected: { language: env.language }, ok });
      console.log(`${ok ? 'PASS' : 'FAIL'} ${label} navigator language asserted before app modules`);
      environments.push({ label, id: env.id, actual: setup ? setup.actual : null, ok });
    }

    /** Open /onboarding and wait for the first product heading. */
    async function openOnboarding({ env, initScript, width, height, url = `${appUrl}/onboarding` }) {
      const page = await openPage({ url, env, initScript, width, height });
      await assertLocaleSetup(page.sessionId, `onboarding[${env.id}]`, env);
      await waitForValue(page.sessionId, `document.querySelector('h1') ? document.querySelector('h1').textContent : null`, { label: 'first heading' });
      return page;
    }

    /** Switch locale through the real storage path (never a shipping control). */
    async function switchLocaleViaStorage(sessionId, locale) {
      return evaluate(
        sessionId,
        `(() => {
          try { localStorage.setItem('happyranch.ui.locale', ${JSON.stringify(locale)}); } catch (error) {}
          window.dispatchEvent(new StorageEvent('storage', { key: 'happyranch.ui.locale', newValue: ${JSON.stringify(locale)} }));
          return true;
        })()`,
      );
    }

    /** Test-only DOM identity observation, matching the established W2a seam. */
    async function tagIdentity(sessionId, selector, prefix) {
      return evaluate(
        sessionId,
        `(() => {
          const el = document.querySelector(${JSON.stringify(selector)});
          if (!el) return null;
          if (!el.dataset.hrIdentity) el.dataset.hrIdentity = ${JSON.stringify(prefix)} + '-' + Math.random().toString(36).slice(2);
          return el.dataset.hrIdentity;
        })()`,
      );
    }

    async function identityOf(sessionId, selector) {
      return evaluate(
        sessionId,
        `(() => { const el = document.querySelector(${JSON.stringify(selector)}); return el ? (el.dataset.hrIdentity || null) : null; })()`,
      );
    }

    async function focusAndTag(sessionId, selector, prefix) {
      return evaluate(
        sessionId,
        `(() => {
          const el = document.querySelector(${JSON.stringify(selector)});
          if (!el) return null;
          el.focus();
          if (!el.dataset.hrIdentity) el.dataset.hrIdentity = ${JSON.stringify(prefix)} + '-' + Math.random().toString(36).slice(2);
          return el.dataset.hrIdentity;
        })()`,
      );
    }

    async function focusAndTagByText(sessionId, selector, text, prefix) {
      return evaluate(
        sessionId,
        `(() => {
          const el = [...document.querySelectorAll(${JSON.stringify(selector)})]
            .find((candidate) => (candidate.textContent || '').trim() === ${JSON.stringify(text)});
          if (!el) return null;
          el.focus();
          if (!el.dataset.hrIdentity) el.dataset.hrIdentity = ${JSON.stringify(prefix)} + '-' + Math.random().toString(36).slice(2);
          return el.dataset.hrIdentity;
        })()`,
      );
    }

    async function activeElementInfo(sessionId) {
      return evaluate(
        sessionId,
        `(() => {
          const el = document.activeElement;
          if (!el) return null;
          return {
            tag: el.tagName,
            id: el.id,
            identity: el.dataset ? (el.dataset.hrIdentity || null) : null,
            value: typeof el.value === 'string' ? el.value : null,
            text: (el.textContent || '').trim(),
          };
        })()`,
      );
    }

    async function blurActiveElement(sessionId) {
      return evaluate(sessionId, `(() => { document.activeElement?.blur(); return true; })()`);
    }

    async function focusIdentity(sessionId, identity) {
      return evaluate(
        sessionId,
        `(() => {
          const el = [...document.querySelectorAll('[data-hr-identity]')]
            .find((candidate) => candidate.dataset.hrIdentity === ${JSON.stringify(identity)});
          if (!el) return null;
          el.focus();
          return el.dataset.hrIdentity;
        })()`,
      );
    }

    function requestWindow(from) {
      const requests = networkRequests.slice(from);
      const mutations = requests.filter((r) => r.method !== 'GET');
      const apiRequests = requests.filter((r) => r.url.includes('/api/'));
      return {
        from,
        count: requests.length,
        mutations,
        settingsPut: requests.filter((r) => r.method === 'PUT' && r.url.includes('/settings/org')).length,
        orgCreate: requests.filter((r) => r.method === 'POST' && r.url.includes('/api/v1/orgs')).length,
        connectOrMint: requests.filter(
          (r) =>
            r.method !== 'GET' &&
            (r.url.includes('/api/v1/auth/registration-token/runtime') ||
              r.url.includes('/api/v1/runtime/') ||
              r.url.includes('/api/v1/executors/runtime/')),
        ).length,
        anyApi: apiRequests.length,
        mutationsAfter: mutations.length,
      };
    }

    function assertLocaleOnlyWindow(label, window) {
      check(`${label} issued zero mutations`, window.mutationsAfter, 0);
      check(`${label} issued no PUT /settings/org`, window.settingsPut, 0);
      check(`${label} issued no duplicate POST /api/v1/orgs`, window.orgCreate, 0);
      check(`${label} issued no connect/mint mutation`, window.connectOrMint, 0);
      check(`${label} issued zero /api/ requests`, window.anyApi, 0);
    }

    async function setViewport(page, width, height) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 600 }, page.sessionId);
      await sleep(300);
    }

    async function setTheme(sessionId, theme) {
      await evaluate(sessionId, `(() => { localStorage.setItem('happyranch.theme', ${JSON.stringify(theme)}); document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)}); return true; })()`);
      await sleep(200);
    }

    // ================= S1: first-run, English (wide light) =================
    {
      SYNTH.orgs = [];
      SYNTH.broken = [];
      SYNTH.prereqs = [];
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.zh, initScript: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('Connect your agentic CLI') && 'ok'`, { label: 'en connect heading' });
      const snap = await snapshot(page.sessionId);
      check('S1 en html.lang', snap.lang, 'en');
      checkIncludes('S1 en connect heading', snap.headings.join(' | '), 'Connect your agentic CLI.');
      checkIncludes('S1 en step eyebrow', snap.bodyText, 'Step 1 of 2 · Connect your agentic CLI');
      checkIncludes('S1 en builtin label', snap.bodyText, 'Pick your agentic CLI');
      checkTruthy('S1 en has Skip affordance', snap.controls.some((c) => c.text.includes('Skip — I’ll connect a CLI later')));
      const geo = await geometry(page.sessionId);
      check('S1 en no document horizontal overflow', geo.docScrollWidth <= geo.vw + 1, true);
      await capture(page, 'en-onboarding-connect-1440-light');
      await closePage(page);
    }

    // ================= S2: first-run, zh-CN (wide light + narrow dark) ======
    {
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('连接你的智能体 CLI') && 'ok'`, { label: 'zh connect heading' });
      const snap = await snapshot(page.sessionId);
      check('S2 zh html.lang', snap.lang, 'zh-CN');
      checkIncludes('S2 zh connect heading', snap.headings.join(' | '), '连接你的智能体 CLI。');
      checkIncludes('S2 zh step eyebrow', snap.bodyText, '第 1 步，共 2 步 · 连接你的智能体 CLI');
      checkIncludes('S2 zh builtin label', snap.bodyText, '选择你的智能体 CLI');
      const fonts = await platformFontsForSelector(page.sessionId, 'h1');
      notes.push({ zhHeadingFonts: fonts.map((f) => f.familyName) });
      checkTruthy('S2 zh heading glyphs use a CJK-capable platform font', fonts.some((f) => isCjkFamily(f.familyName)));
      const geo = await geometry(page.sessionId);
      check('S2 zh 1440 no document horizontal overflow', geo.docScrollWidth <= geo.vw + 1, true);
      await capture(page, 'zh-onboarding-connect-1440-light');

      await setViewport(page, 390, 844);
      await setTheme(page.sessionId, 'dark');
      const geo390 = await geometry(page.sessionId);
      check('S2 zh 390 dark applied', await evaluate(page.sessionId, `document.documentElement.getAttribute('data-theme')`), 'dark');
      check('S2 zh 390 no document horizontal overflow', geo390.docScrollWidth <= geo390.vw + 1, true);
      await capture(page, 'zh-onboarding-connect-390-dark');
      await closePage(page);
    }

    // ================= S3: returning user welcome, zh-CN ====================
    {
      SYNTH.orgs = [{ slug: 'demo-org', root: '/runtime/demo-org' }];
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('创建另一个组织') && 'ok'`, { label: 'zh welcome heading' });
      const snap = await snapshot(page.sessionId);
      checkIncludes('S3 zh returning heading', snap.headings.join(' | '), '创建另一个组织');
      checkTruthy('S3 zh returning has create CTA', snap.controls.some((c) => c.text.includes('创建另一个组织')));
      checkIncludes('S3 zh timing copy', snap.bodyText, '只需几秒钟。');
      await capture(page, 'zh-onboarding-welcome-1440-light');
      await closePage(page);
    }

    // ================= S4: create validation + mapped error + switch ========
    {
      SYNTH.orgs = [{ slug: 'demo-org', root: '/runtime/demo-org' }];
      SYNTH.createCalls = 0;
      SYNTH.createOrg = { status: 409, body: { code: 'org_exists', message: 'org_exists' } };
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('Create another org') && 'ok'`, { label: 'en welcome' });
      await clickByText(page.sessionId, 'Create another org');
      await waitForValue(page.sessionId, `document.querySelector('#onboarding-slug') ? 'ok' : null`, { label: 'slug input' });
      // Invalid slug keeps submit disabled (client validation).
      await setInputValue(page.sessionId, '#onboarding-slug', 'Bad Slug!');
      const invalidDisabled = await evaluate(page.sessionId, `(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Create org'); return b ? b.disabled : null; })()`);
      check('S4 invalid slug disables Create org', invalidDisabled, true);
      // Valid slug → mapped error.
      await setInputValue(page.sessionId, '#onboarding-slug', 'taken-org');
      await clickByText(page.sessionId, 'Create org');
      await waitForValue(page.sessionId, `document.body.textContent.includes('already exists') && 'ok'`, { label: 'mapped error' });
      const before = await snapshot(page.sessionId);
      const slugIdentity = await tagIdentity(page.sessionId, '#onboarding-slug', 's4-slug');
      const createSurfaceIdentity = await tagIdentity(page.sessionId, 'section', 's4-create');
      await capture(page, 'en-onboarding-create-mapped-error-1440-light');
      const slugFocus = await focusAndTag(page.sessionId, '#onboarding-slug', 's4-slug');
      checkIncludes('S4 en mapped error', before.bodyText, 'An org with slug "taken-org" already exists.');
      check('S4 en create surface open', before.slugValue, 'taken-org');
      check('S4 en slug input has stable identity', slugIdentity, slugFocus);
      check('S4 en actual activeElement is retained slug input', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      check('S4 single create call before switch', SYNTH.createCalls, 1);

      // Switch en→zh: mapped copy re-translates without remount/resubmission.
      const beforeZh = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'zh-CN');
      await waitForValue(page.sessionId, `document.body.textContent.includes('已存在') && 'ok'`, { label: 'zh mapped error' });
      const after = await snapshot(page.sessionId);
      const zhWindow = requestWindow(beforeZh);
      checkIncludes('S4 zh mapped error after switch', after.bodyText, '标识符为 "taken-org" 的组织已存在。');
      check('S4 zh create surface remains open', after.slugValue, 'taken-org');
      check('S4 zh create surface node identity retained', await identityOf(page.sessionId, 'section'), createSurfaceIdentity);
      check('S4 zh slug input node identity retained', await identityOf(page.sessionId, '#onboarding-slug'), slugIdentity);
      check('S4 zh actual activeElement retained', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      check('S4 exactly one original create after en→zh-CN', SYNTH.createCalls, 1);
      assertLocaleOnlyWindow('S4 en→zh-CN switch', zhWindow);

      const beforeEn = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'en');
      await waitForValue(page.sessionId, `document.body.textContent.includes('already exists') && 'ok'`, { label: 'en mapped error again' });
      const enAgain = await snapshot(page.sessionId);
      const enWindow = requestWindow(beforeEn);
      checkIncludes('S4 en mapped error after reverse switch', enAgain.bodyText, 'An org with slug "taken-org" already exists.');
      check('S4 en create surface remains open after reverse switch', enAgain.slugValue, 'taken-org');
      check('S4 en create surface node identity retained after reverse switch', await identityOf(page.sessionId, 'section'), createSurfaceIdentity);
      check('S4 en slug input node identity retained after reverse switch', await identityOf(page.sessionId, '#onboarding-slug'), slugIdentity);
      check('S4 en actual activeElement retained after reverse switch', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      check('S4 exactly one original create after round trip', SYNTH.createCalls, 1);
      assertLocaleOnlyWindow('S4 zh-CN→en switch', enWindow);
      await closePage(page);
    }

    // ================= S5: unknown error diagnostic preserved across switch ===
    // The real client wraps a 5xx in `ApiError` whose `.message` is the
    // app-owned `API <status>` diagnostic (the daemon's `detail` is not the raw
    // branch input). That diagnostic must NOT be re-translated: it stays
    // byte-for-byte while the surrounding chrome re-translates.
    {
      SYNTH.orgs = [{ slug: 'demo-org', root: '/runtime/demo-org' }];
      SYNTH.createCalls = 0;
      SYNTH.createOrg = { status: 500, body: { message: RAW_ERROR } };
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('Create another org') && 'ok'`, { label: 'en welcome raw' });
      await clickByText(page.sessionId, 'Create another org');
      await waitForValue(page.sessionId, `document.querySelector('#onboarding-slug') ? 'ok' : null`, { label: 'slug input raw' });
      await setInputValue(page.sessionId, '#onboarding-slug', 'raw-org');
      await clickByText(page.sessionId, 'Create org');
      await waitForValue(page.sessionId, `document.body.textContent.includes('API 500') && 'ok'`, { label: 'unknown error en' });
      const enSnap = await snapshot(page.sessionId);
      const slugIdentity = await tagIdentity(page.sessionId, '#onboarding-slug', 's5-slug');
      const createSurfaceIdentity = await tagIdentity(page.sessionId, 'section', 's5-create');
      const slugFocus = await focusAndTag(page.sessionId, '#onboarding-slug', 's5-slug');
      check('S5 en unknown diagnostic byte-exact', await evaluate(page.sessionId, `document.querySelector('[role="alert"]')?.textContent?.trim() || null`), 'API 500');
      check('S5 en does not show the localized generic copy', enSnap.bodyText.includes('Could not create org.'), false);
      check('S5 en create surface open with raw-org', enSnap.slugValue, 'raw-org');
      check('S5 en slug input has stable identity', slugIdentity, slugFocus);
      check('S5 en actual activeElement is retained slug input', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      check('S5 single create call before switch', SYNTH.createCalls, 1);

      const beforeZh = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'zh-CN');
      await waitForValue(page.sessionId, `document.body.textContent.includes('为组织命名') && 'ok'`, { label: 'zh create heading' });
      const zhSnap = await snapshot(page.sessionId);
      const zhWindow = requestWindow(beforeZh);
      check('S5 zh unknown diagnostic remains byte-exact', await evaluate(page.sessionId, `document.querySelector('[role="alert"]')?.textContent?.trim() || null`), 'API 500');
      check('S5 zh does not translate the raw diagnostic', zhSnap.bodyText.includes('无法创建组织。'), false);
      check('S5 zh create surface remains open with raw-org', zhSnap.slugValue, 'raw-org');
      check('S5 zh create surface node identity retained', await identityOf(page.sessionId, 'section'), createSurfaceIdentity);
      check('S5 zh slug input node identity retained', await identityOf(page.sessionId, '#onboarding-slug'), slugIdentity);
      check('S5 zh actual activeElement retained', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      check('S5 exactly one original create after en→zh-CN', SYNTH.createCalls, 1);
      assertLocaleOnlyWindow('S5 en→zh-CN switch', zhWindow);

      const beforeEn = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'en');
      await waitForValue(page.sessionId, `document.body.textContent.includes('Name your org') && 'ok'`, { label: 'en raw create heading again' });
      const enAgain = await snapshot(page.sessionId);
      const enWindow = requestWindow(beforeEn);
      check('S5 en unknown diagnostic remains byte-exact after reverse switch', await evaluate(page.sessionId, `document.querySelector('[role="alert"]')?.textContent?.trim() || null`), 'API 500');
      check('S5 en create surface remains open after reverse switch', enAgain.slugValue, 'raw-org');
      check('S5 en create surface node identity retained after reverse switch', await identityOf(page.sessionId, 'section'), createSurfaceIdentity);
      check('S5 en slug input node identity retained after reverse switch', await identityOf(page.sessionId, '#onboarding-slug'), slugIdentity);
      check('S5 en actual activeElement retained after reverse switch', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      check('S5 exactly one original create after round trip', SYNTH.createCalls, 1);
      assertLocaleOnlyWindow('S5 zh-CN→en switch', enWindow);
      await closePage(page);
    }

    // ================= S6: success state (slug preserved across switch) =====
    {
      SYNTH.orgs = [{ slug: 'demo-org', root: '/runtime/demo-org' }];
      SYNTH.createCalls = 0;
      SYNTH.createOrg = { status: 200, body: { slug: 'created-org' } };
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('Create another org') && 'ok'`, { label: 'en welcome success' });
      await clickByText(page.sessionId, 'Create another org');
      await waitForValue(page.sessionId, `document.querySelector('#onboarding-slug') ? 'ok' : null`, { label: 'slug input success' });
      await setInputValue(page.sessionId, '#onboarding-slug', 'created-org');
      await clickByText(page.sessionId, 'Create org');
      await waitForValue(page.sessionId, `document.body.textContent.includes('is ready') && 'ok'`, { label: 'success state' });
      const enSnap = await snapshot(page.sessionId);
      const successIdentity = await tagIdentity(page.sessionId, 'section', 's6-success');
      const successControlIdentity = await focusAndTag(page.sessionId, 'section button', 's6-success-control');
      checkIncludes('S6 en success heading', enSnap.headings.join(' | '), 'Org created-org is ready.');
      checkIncludes('S6 en raw created-org preserved', enSnap.bodyText, 'created-org');
      check('S6 en success phase has no create input', enSnap.slugValue, null);
      check('S6 en actual activeElement is success control', (await activeElementInfo(page.sessionId))?.identity, successControlIdentity);
      check('S6 single create call before switch', SYNTH.createCalls, 1);

      const beforeZh = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'zh-CN');
      await waitForValue(page.sessionId, `document.body.textContent.includes('已就绪') && 'ok'`, { label: 'zh success' });
      const zhSnap = await snapshot(page.sessionId);
      const zhWindow = requestWindow(beforeZh);
      checkIncludes('S6 zh success heading', zhSnap.headings.join(' | '), '组织 created-org 已就绪。');
      checkIncludes('S6 zh success raw slug preserved', zhSnap.bodyText, 'created-org');
      check('S6 zh remains in success phase', zhSnap.slugValue, null);
      check('S6 zh success container identity retained', await identityOf(page.sessionId, 'section'), successIdentity);
      check('S6 zh success control identity retained', await identityOf(page.sessionId, 'section button'), successControlIdentity);
      check('S6 zh actual activeElement retained', (await activeElementInfo(page.sessionId))?.identity, successControlIdentity);
      check('S6 exactly one original create after en→zh-CN', SYNTH.createCalls, 1);
      assertLocaleOnlyWindow('S6 en→zh-CN switch', zhWindow);
      await blurActiveElement(page.sessionId);
      await capture(page, 'zh-onboarding-success-1440-light');
      check('S6 zh success control refocused before reverse seam', await focusIdentity(page.sessionId, successControlIdentity), successControlIdentity);

      const beforeEn = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'en');
      await waitForValue(page.sessionId, `document.body.textContent.includes('Org created-org is ready.') && 'ok'`, { label: 'en success again' });
      const enAgain = await snapshot(page.sessionId);
      const enWindow = requestWindow(beforeEn);
      checkIncludes('S6 en success heading after reverse switch', enAgain.headings.join(' | '), 'Org created-org is ready.');
      checkIncludes('S6 en raw created-org preserved after reverse switch', enAgain.bodyText, 'created-org');
      check('S6 en remains in success phase after reverse switch', enAgain.slugValue, null);
      check('S6 en success container identity retained after reverse switch', await identityOf(page.sessionId, 'section'), successIdentity);
      check('S6 en success control identity retained after reverse switch', await identityOf(page.sessionId, 'section button'), successControlIdentity);
      check('S6 en actual activeElement retained after reverse switch', (await activeElementInfo(page.sessionId))?.identity, successControlIdentity);
      check('S6 exactly one original create after round trip', SYNTH.createCalls, 1);
      assertLocaleOnlyWindow('S6 zh-CN→en switch', enWindow);
      await closePage(page);
    }

    // ================= S7: broken-org list (raw slug/error verbatim) ========
    {
      SYNTH.orgs = [];
      SYNTH.broken = [{ slug: BROKEN_SLUG, error: BROKEN_ERROR }];
      SYNTH.prereqs = [];
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('zh-CN')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.body.textContent.includes('连接你的智能体 CLI') ? 'ok' : null`, { label: 'zh connect with broken' });
      // Skip connect → Step 2 welcome (existingCount 0) so the broken list renders.
      await clickByText(page.sessionId, '我稍后再连接 CLI');
      await waitForValue(page.sessionId, `document.body.textContent.includes('加载失败') && 'ok'`, { label: 'zh broken heading' });
      const snap = await snapshot(page.sessionId);
      checkIncludes('S7 zh broken heading', snap.bodyText, '1 个组织加载失败');
      checkIncludes('S7 zh broken raw slug verbatim', snap.bodyText, BROKEN_SLUG);
      checkIncludes('S7 zh broken raw error verbatim', snap.bodyText, BROKEN_ERROR);
      checkIncludes('S7 zh broken localized copy', snap.bodyText, '复制错误');
      await capture(page, 'zh-onboarding-broken-1440-light');
      await closePage(page);
    }

    // ================= S8: built-in waiting prompt preserved en→zh→en =======
    {
      SYNTH.orgs = [];
      SYNTH.broken = [];
      SYNTH.prereqs = [{ tool: 'claude', present: false, path: null, hint: 'Register Claude Code' }];
      SYNTH.minted = 0;
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('#builtin-kind') ? 'ok' : null`, { label: 'builtin select' });
      await selectOption(page.sessionId, '#builtin-kind', 'claude');
      await clickByText(page.sessionId, 'Generate connect prompt');
      await waitForValue(page.sessionId, `document.querySelector('pre') ? 'ok' : null`, { label: 'prompt pre' });
      const enSnap = await snapshot(page.sessionId);
      const promptEn = enSnap.preText;
      const stepIds = ['workspace_access', 'loopback_reachable', 'cli_callback', 'emit_envelope'];
      checkTruthy('S8 en prompt present', promptEn && promptEn.includes('hr_tok_W2B_EVIDENCE'));
      checkIncludes('S8 en selected built-in claude retained in prompt', promptEn, "built-in 'claude' CLI");
      check('S8 en raw step ids present', stepIds.filter((id) => enSnap.bodyText.includes(id)), stepIds);
      check('S8 en custom mode is not open', enSnap.adapterNameValue, null);
      check('S8 exactly one mint before switch', SYNTH.minted, 1);
      await capture(page, 'en-onboarding-connect-waiting-1440-light');

      const promptIdentity = await tagIdentity(page.sessionId, 'pre', 's8-prompt');
      const waitingIdentity = await tagIdentity(page.sessionId, '[aria-label="Waiting for your CLI"]', 's8-waiting');
      const waitingControlIdentity = await focusAndTagByText(page.sessionId, 'button', 'Copy prompt', 's8-waiting-control');
      check('S8 en waiting phase node observed', Boolean(waitingIdentity), true);
      check('S8 en actual activeElement is waiting control', (await activeElementInfo(page.sessionId))?.identity, waitingControlIdentity);

      const beforeZh = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'zh-CN');
      await waitForValue(page.sessionId, `document.body.textContent.includes('正在等待') && 'ok'`, { label: 'zh waiting' });
      const zhSnap = await snapshot(page.sessionId);
      const zhWindow = requestWindow(beforeZh);
      check('S8 zh prompt bytes identical to en', zhSnap.preText, promptEn);
      checkIncludes('S8 zh raw token bytes preserved', zhSnap.preText, 'hr_tok_W2B_EVIDENCE');
      checkIncludes('S8 zh localized step label', zhSnap.bodyText, '读取其工作区与技能');
      check('S8 zh raw step ids preserved', stepIds.filter((id) => zhSnap.bodyText.includes(id)), stepIds);
      check('S8 zh selected built-in mode retained', zhSnap.adapterNameValue, null);
      check('S8 zh prompt node identity retained', await identityOf(page.sessionId, 'pre'), promptIdentity);
      check('S8 zh waiting phase node identity retained', await identityOf(page.sessionId, '[aria-label="正在等待你的 CLI"]'), waitingIdentity);
      check('S8 zh actual activeElement retained', (await activeElementInfo(page.sessionId))?.identity, waitingControlIdentity);
      check('S8 exactly one original mint after en→zh-CN', SYNTH.minted, 1);
      assertLocaleOnlyWindow('S8 en→zh-CN switch', zhWindow);
      await blurActiveElement(page.sessionId);
      // Preserve the established screenshot framing after the focus assertion;
      // the same retained control is explicitly refocused for the reverse seam.
      await evaluate(page.sessionId, `(() => {
        document.scrollingElement.scrollTop = 0;
        for (const element of document.querySelectorAll('*')) {
          if (element.scrollHeight > element.clientHeight) element.scrollTop = 0;
        }
        return true;
      })()`);
      await capture(page, 'zh-onboarding-connect-waiting-1440-light');
      check('S8 zh waiting control refocused before reverse seam', await focusIdentity(page.sessionId, waitingControlIdentity), waitingControlIdentity);

      const beforeEn = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'en');
      await waitForValue(page.sessionId, `document.body.textContent.includes('Waiting for') && 'ok'`, { label: 'en waiting again' });
      const enAgain = await snapshot(page.sessionId);
      const enWindow = requestWindow(beforeEn);
      check('S8 en→zh→en prompt bytes preserved', enAgain.preText, promptEn);
      checkIncludes('S8 en raw token bytes preserved after reverse switch', enAgain.preText, 'hr_tok_W2B_EVIDENCE');
      check('S8 en raw step ids preserved after reverse switch', stepIds.filter((id) => enAgain.bodyText.includes(id)), stepIds);
      check('S8 en selected built-in mode retained after reverse switch', enAgain.adapterNameValue, null);
      check('S8 en prompt node identity retained after reverse switch', await identityOf(page.sessionId, 'pre'), promptIdentity);
      check('S8 en waiting phase node identity retained after reverse switch', await identityOf(page.sessionId, '[aria-label="Waiting for your CLI"]'), waitingIdentity);
      check('S8 en actual activeElement retained after reverse switch', (await activeElementInfo(page.sessionId))?.identity, waitingControlIdentity);
      check('S8 still exactly one mint after round trip', SYNTH.minted, 1);
      assertLocaleOnlyWindow('S8 zh-CN→en switch', enWindow);
      await closePage(page);
    }

    // ================= S9: custom mode typed name preserved across switch ====
    {
      SYNTH.orgs = [];
      SYNTH.broken = [];
      SYNTH.prereqs = [];
      SYNTH.minted = 0;
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('Connect your agentic CLI') && 'ok'`, { label: 'en connect custom' });
      await clickByText(page.sessionId, 'Connect a custom CLI instead');
      await waitForValue(page.sessionId, `document.querySelector('#adapter-name') ? 'ok' : null`, { label: 'adapter name input' });
      await setInputValue(page.sessionId, '#adapter-name', 'my-cli');
      const inputIdentity = await tagIdentity(page.sessionId, '#adapter-name', 's9-adapter-name');
      const formIdentity = await tagIdentity(page.sessionId, 'form', 's9-custom-form');
      const inputFocus = await focusAndTag(page.sessionId, '#adapter-name', 's9-adapter-name');
      const enSnap = await snapshot(page.sessionId);
      checkIncludes('S9 en custom mode banner present', enSnap.bodyText, 'Create a custom adapter wrapper');
      check('S9 en custom mode open with my-cli', enSnap.adapterNameValue, 'my-cli');
      check('S9 en adapter input has stable identity', inputIdentity, inputFocus);
      check('S9 en actual activeElement is adapter-name', (await activeElementInfo(page.sessionId))?.identity, inputIdentity);
      check('S9 no mint before locale switch', SYNTH.minted, 0);

      const beforeZh = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'zh-CN');
      await waitForValue(page.sessionId, `document.body.textContent.includes('创建自定义适配器包装器') && 'ok'`, { label: 'zh adapter banner' });
      const snap = await snapshot(page.sessionId);
      const zhWindow = requestWindow(beforeZh);
      checkIncludes('S9 zh custom banner translated', snap.bodyText, '创建自定义适配器包装器');
      checkIncludes('S9 zh authored adapter suffix stays raw', snap.bodyText, '<name>-adapter');
      check('S9 zh custom mode remains open with my-cli', snap.adapterNameValue, 'my-cli');
      check('S9 zh custom form node identity retained', await identityOf(page.sessionId, 'form'), formIdentity);
      check('S9 zh adapter input node identity retained', await identityOf(page.sessionId, '#adapter-name'), inputIdentity);
      check('S9 zh actual activeElement retained', (await activeElementInfo(page.sessionId))?.identity, inputIdentity);
      check('S9 still no mint after en→zh-CN', SYNTH.minted, 0);
      assertLocaleOnlyWindow('S9 en→zh-CN switch', zhWindow);
      await capture(page, 'zh-onboarding-connect-custom-1440-light');

      const beforeEn = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'en');
      await waitForValue(page.sessionId, `document.body.textContent.includes('Create a custom adapter wrapper') && 'ok'`, { label: 'en adapter banner again' });
      const enAgain = await snapshot(page.sessionId);
      const enWindow = requestWindow(beforeEn);
      checkIncludes('S9 en custom banner translated after reverse switch', enAgain.bodyText, 'Create a custom adapter wrapper');
      checkIncludes('S9 en authored adapter suffix stays raw after reverse switch', enAgain.bodyText, '<name>-adapter');
      check('S9 en custom mode remains open after reverse switch', enAgain.adapterNameValue, 'my-cli');
      check('S9 en custom form node identity retained after reverse switch', await identityOf(page.sessionId, 'form'), formIdentity);
      check('S9 en adapter input node identity retained after reverse switch', await identityOf(page.sessionId, '#adapter-name'), inputIdentity);
      check('S9 en actual activeElement retained after reverse switch', (await activeElementInfo(page.sessionId))?.identity, inputIdentity);
      check('S9 no mint after round trip', SYNTH.minted, 0);
      assertLocaleOnlyWindow('S9 zh-CN→en switch', enWindow);
      await closePage(page);
    }

    // ================= S10: prereq panel localized + raw values =============
    {
      SYNTH.orgs = [{ slug: 'demo-org', root: '/runtime/demo-org' }];
      SYNTH.prereqs = [
        { tool: 'claude', present: true, path: '/usr/bin/claude', hint: '' },
        { tool: 'codex', present: false, path: null, hint: 'Register Codex' },
      ];
      SYNTH.createCalls = 0;
      SYNTH.minted = 0;
      const page = await openOnboarding({ env: DEV_ENVIRONMENTS.en, initScript: `${seedLocale('en')}\n${seedTheme('light')}` });
      await waitForValue(page.sessionId, `document.querySelector('h1') && document.querySelector('h1').textContent.includes('Create another org') && 'ok'`, { label: 'en welcome prereq' });
      await clickByText(page.sessionId, 'Create another org');
      await waitForValue(page.sessionId, `document.body.textContent.includes('1 of 2 tools registered') && 'ok'`, { label: 'en prereq summary' });
      const createIdentity = await tagIdentity(page.sessionId, 'section', 's10-create');
      const prereqIdentity = await tagIdentity(page.sessionId, 'section[aria-label="Executor readiness"]', 's10-prereq');
      const slugIdentity = await focusAndTag(page.sessionId, '#onboarding-slug', 's10-slug');
      const enSnap = await snapshot(page.sessionId);
      checkIncludes('S10 en prereq summary', enSnap.bodyText, '1 of 2 tools registered');
      checkIncludes('S10 en raw tool path', enSnap.bodyText, '/usr/bin/claude');
      checkIncludes('S10 en raw claude tool name', enSnap.bodyText, 'claude');
      checkIncludes('S10 en raw codex tool name', enSnap.bodyText, 'codex');
      checkIncludes('S10 en raw prereq hint', enSnap.bodyText, 'Register Codex');
      check('S10 en open create surface has empty slug', enSnap.slugValue, '');
      check('S10 en actual activeElement is create input', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      check('S10 en prereq phase node observed', Boolean(prereqIdentity), true);

      const beforeZh = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'zh-CN');
      await waitForValue(page.sessionId, `document.body.textContent.includes('已注册 1/2 个工具') && 'ok'`, { label: 'zh prereq summary' });
      const snap = await snapshot(page.sessionId);
      const zhWindow = requestWindow(beforeZh);
      checkIncludes('S10 zh prereq summary', snap.bodyText, '已注册 1/2 个工具');
      checkIncludes('S10 raw tool path preserved', snap.bodyText, '/usr/bin/claude');
      checkIncludes('S10 raw claude tool name preserved', snap.bodyText, 'claude');
      checkIncludes('S10 raw codex tool name preserved', snap.bodyText, 'codex');
      checkIncludes('S10 raw prereq hint preserved', snap.bodyText, 'Register Codex');
      checkIncludes('S10 zh not-registered pill', snap.bodyText, '未注册');
      check('S10 zh create surface remains open', snap.slugValue, '');
      check('S10 zh create surface node identity retained', await identityOf(page.sessionId, 'section'), createIdentity);
      check('S10 zh create input node identity retained', await identityOf(page.sessionId, '#onboarding-slug'), slugIdentity);
      check('S10 zh prereq phase node identity retained', await identityOf(page.sessionId, 'section[aria-label="执行器就绪情况"]'), prereqIdentity);
      check('S10 zh actual activeElement retained', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      assertLocaleOnlyWindow('S10 en→zh-CN switch', zhWindow);
      await capture(page, 'zh-onboarding-create-prereqs-1440-light');

      const beforeEn = networkRequests.length;
      await switchLocaleViaStorage(page.sessionId, 'en');
      await waitForValue(page.sessionId, `document.body.textContent.includes('1 of 2 tools registered') && 'ok'`, { label: 'en prereq summary again' });
      const enAgain = await snapshot(page.sessionId);
      const enWindow = requestWindow(beforeEn);
      checkIncludes('S10 en prereq summary after reverse switch', enAgain.bodyText, '1 of 2 tools registered');
      checkIncludes('S10 en raw tool path preserved after reverse switch', enAgain.bodyText, '/usr/bin/claude');
      checkIncludes('S10 en raw claude tool name preserved after reverse switch', enAgain.bodyText, 'claude');
      checkIncludes('S10 en raw codex tool name preserved after reverse switch', enAgain.bodyText, 'codex');
      checkIncludes('S10 en raw prereq hint preserved after reverse switch', enAgain.bodyText, 'Register Codex');
      check('S10 en create surface remains open after reverse switch', enAgain.slugValue, '');
      check('S10 en create surface node identity retained after reverse switch', await identityOf(page.sessionId, 'section'), createIdentity);
      check('S10 en create input node identity retained after reverse switch', await identityOf(page.sessionId, '#onboarding-slug'), slugIdentity);
      check('S10 en prereq phase node identity retained after reverse switch', await identityOf(page.sessionId, 'section[aria-label="Executor readiness"]'), prereqIdentity);
      check('S10 en actual activeElement retained after reverse switch', (await activeElementInfo(page.sessionId))?.identity, slugIdentity);
      assertLocaleOnlyWindow('S10 zh-CN→en switch', enWindow);
      await closePage(page);
    }

    finalizeReceipt();
  } finally {
    if (cdp) cdp.close();
    if (chrome && !chrome.killed) chrome.kill('SIGKILL');
    appServer.server.close();
    if (chromeStderr.trim()) notes.push({ chromeStderrTail: chromeStderr.slice(-2000) });
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
