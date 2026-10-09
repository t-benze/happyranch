/** Fixed real-daemon browser observations. No mock API, interception or app instrumentation. */
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

assert.equal(process.argv.length, 3);
assert.equal(process.versions.node, '24.19.0');
const binding = JSON.parse(readFileSync(process.argv[2], 'utf8'));
assert.deepEqual(Object.keys(binding).sort(), ['base', 'devtools', 'out', 'platform']);
assert.ok(['linux', 'darwin'].includes(binding.platform));
const origin = new URL(binding.base);
assert.equal(origin.hostname, '127.0.0.1');
assert.equal(origin.pathname, '/');
const debug = new URL(binding.devtools);
assert.equal(debug.hostname, '127.0.0.1');
assert.equal(debug.protocol, 'ws:');
const deadline = Date.now() + 180000;
const results = { scope: 'real isolated wheel daemon and ordinary Vite distribution', cases: [], status: 'failed' };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

class CDP {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.next = 0;
    this.pending = new Map();
    this.events = [];
    this.ready = new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', () => reject(new Error('CDP open failed')), { once: true });
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        clearTimeout(pending.timer); this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
      } else if (message.method === 'Network.responseReceived') {
        assert.ok(this.events.length < 2000, 'bounded HTTP observation cap');
        const response = message.params.response;
        const url = new URL(response.url);
        // Retain status/path only. Never record headers, auth, response bodies or queries.
        if (url.origin === origin.origin) this.events.push({ session: message.sessionId,
          path: url.pathname, status: response.status, type: message.params.type });
      }
    });
  }
  async send(method, params = {}, sessionId) {
    await this.ready;
    assert.ok(Date.now() < deadline, 'finite browser deadline');
    const id = ++this.next;
    const answer = new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout ${method}`)); }, 10000);
      this.pending.set(id, { resolve, reject, timer });
    });
    this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    return answer;
  }
  close() { this.socket.close(); }
}

const cdp = new CDP(binding.devtools);
async function evaluate(session, expression) {
  const value = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, session);
  if (value.exceptionDetails) {
    // Bounded primitive error identity; no exception objects or response bodies.
    const detail = value.exceptionDetails;
    const diagnostic = { text: String(detail.text).slice(0, 256),
      type: String(detail.exception?.className || 'unknown').slice(0, 128),
      line: detail.lineNumber, column: detail.columnNumber };
    throw new Error('browser evaluation exception: ' + JSON.stringify(diagnostic));
  }
  return value.result.value;
}
async function wait(session, expression) {
  const end = Math.min(Date.now() + 15000, deadline);
  while (Date.now() < end) {
    const value = await evaluate(session, expression);
    if (value) return value;
    await sleep(100);
  }
  throw new Error(`browser condition timed out: ${expression}`);
}
async function key(session, name, code) {
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, code: name,
    windowsVirtualKeyCode: code, nativeVirtualKeyCode: code }, session);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code: name,
    windowsVirtualKeyCode: code, nativeVirtualKeyCode: code }, session);
}
async function screenshot(session, name) {
  const value = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, session);
  const data = Buffer.from(value.data, 'base64');
  assert.ok(data.length > 1000 && data.length <= 4 * 1024 * 1024);
  assert.equal(data.readUInt32BE(0), 0x89504e47);
  writeFileSync(join(binding.out, name), data, { mode: 0o600 });
  return { name, bytes: data.length, width: data.readUInt32BE(16), height: data.readUInt32BE(20) };
}
const absence = String.raw`(() => {
  const controls = [...document.querySelectorAll('a,button,[role="button"],[role="tab"]')]
    .map(e => ({ text:(e.innerText || '').trim(), label:e.getAttribute('aria-label') || '', href:e.getAttribute('href') || '' }));
  const forbidden = controls.filter(e => /assistant|助手|a-mode/i.test(e.text+' '+e.label) || /\/assistant(?:\/|$)/.test(e.href));
  const docks = [...document.querySelectorAll('[data-testid],[aria-label]')]
    .filter(e => /assistant.?dock|assistant.?launcher|assistant.?toggle/i.test((e.getAttribute('data-testid') || '')+' '+(e.getAttribute('aria-label') || '')));
  return { forbidden, dockCount:docks.length, controlCount:controls.length, htmlLang:document.documentElement.lang,
    location:location.pathname, navLinks:controls.filter(e => e.href.startsWith('/orgs/test/')) };
})()`;

try {
  if (binding.platform === 'linux') {
    const targetId = (await cdp.send('Target.createTarget', { url: 'chrome://sandbox' })).targetId;
    try {
      const session = (await cdp.send('Target.attachToTarget', { targetId, flatten: true })).sessionId;
      await cdp.send('Runtime.enable', {}, session);
      const status = await wait(session, `document.body?.innerText.includes('Seccomp-BPF') && document.body.innerText`);
      assert.ok(status.length <= 16384, 'bounded Chrome sandbox status');
      results.linuxSandbox = { text: status, namespace: /Namespace sandbox\s+Yes/.test(status),
        seccomp: /Seccomp-BPF sandbox\s+Yes/.test(status), disablingFlags: [] };
      assert.ok(results.linuxSandbox.namespace && results.linuxSandbox.seccomp,
        'default unprivileged namespace and seccomp sandbox must be active');
    } finally {
      await cdp.send('Target.closeTarget', { targetId });
    }
  }
  for (const locale of ['en', 'zh-CN']) for (const [width, height] of [[390, 844], [1440, 900]]) {
    const row = { locale, width, height, status: 'failed', screenshots: [] };
    results.cases.push(row);
    let context;
    row.phase = 'context';
    try {
      context = (await cdp.send('Target.createBrowserContext')).browserContextId;
      const targetId = (await cdp.send('Target.createTarget', { url: 'about:blank', browserContextId: context })).targetId;
      const session = (await cdp.send('Target.attachToTarget', { targetId, flatten: true })).sessionId;
      await cdp.send('Page.enable', {}, session);
      await cdp.send('Runtime.enable', {}, session);
      await cdp.send('Network.enable', {}, session);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, session);
      await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source:
        `if (location.origin===${JSON.stringify(origin.origin)}) localStorage.setItem('happyranch.ui.locale', ${JSON.stringify(locale)});` }, session);
      row.phase = 'settings-navigation';
      await cdp.send('Page.navigate', { url: binding.base + 'orgs/test/settings/daemon-capacity' }, session);
      await wait(session, `document.documentElement?.lang===${JSON.stringify(locale)} && !!document.querySelector('a[href="/orgs/test/settings/preferences"]')`);
      await wait(session, `!!document.body && !document.body.innerText.includes(${JSON.stringify(locale === 'en' ? 'Loading settings' : '正在加载设置')})`);
      row.phase = 'settings-observation';
      row.settings = await evaluate(session, absence);
      assert.deepEqual(row.settings.forbidden, []);
      assert.equal(row.settings.dockCount, 0);
      assert.ok(row.settings.navLinks.some(link => link.href === '/orgs/test/settings/preferences'));
      row.screenshots.push(await screenshot(session, `${locale}-${width}-settings.png`));
      // Focus via native Tab events, then activate the actual Preferences link with Enter.
      let reached = false;
      row.phase = 'keyboard-navigation';
      for (let attempt = 0; attempt < 60; attempt++) {
        await key(session, 'Tab', 9);
        if (await evaluate(session, `document.activeElement?.getAttribute('href')==='/orgs/test/settings/preferences'`)) {
          reached = true; break;
        }
      }
      assert.ok(reached, 'Preferences link must be reachable by Tab');
      await key(session, 'Enter', 13);
      await wait(session, `location.pathname==='/orgs/test/settings/preferences'`);
      row.keyboard = { tabReachedPreferences: reached, enterNavigated: true };
      await key(session, 'Escape', 27);
      row.preferences = await evaluate(session, absence);
      assert.deepEqual(row.preferences.forbidden, []);
      assert.equal(row.preferences.dockCount, 0);
      // Actual retired transport from the browser. No fake backend or auth injection.
      row.phase = 'retired-websocket';
      row.retiredWebSocket = await evaluate(session, `new Promise((resolve,reject) => {
        const ws=new WebSocket(location.origin.replace('http:','ws:')+'/api/v1/assistant/a-mode');
        const timer=setTimeout(()=>{ws.close();reject(new Error('retired WS deadline'));},5000);
        ws.onopen=()=>{clearTimeout(timer);ws.close();resolve({accepted:true});};
        ws.onclose=e=>{clearTimeout(timer);resolve({accepted:false,code:e.code});};
      })`);
      assert.equal(row.retiredWebSocket.accepted, false);
      row.phase = 'tasks-navigation';
      await evaluate(session, `(() => { const a=document.querySelector('a[href="/orgs/test/tasks"]'); if(!a)throw new Error('Tasks navigation absent'); a.click(); return true; })()`);
      await wait(session, `location.pathname==='/orgs/test/tasks' && !!document.querySelector('aside')`);
      row.navigation = await evaluate(session, absence);
      assert.deepEqual(row.navigation.forbidden, []);
      assert.equal(row.navigation.dockCount, 0);
      row.screenshots.push(await screenshot(session, `${locale}-${width}-tasks.png`));
      row.phase = 'http-observation';
      row.http = cdp.events.filter(event => event.session === session).map(({ session: _session, ...event }) => event);
      for (const path of ['/api/v1/auth/bootstrap', '/api/v1/orgs', '/api/v1/orgs/test/settings'])
        assert.ok(row.http.some(event => event.path === path && event.status === 200), `real HTTP ${path}`);
      assert.ok(!row.http.some(event => event.path.startsWith('/api/v1/assistant')), 'UI must not call retired Assistant HTTP');
      assert.equal(row.screenshots.length, 2);
      assert.ok(row.screenshots.every(shot => shot.width === width && shot.height === height));
      row.phase = 'complete';
      row.status = 'passed';
    } catch (error) {
      row.error = { type: error.name, message: error.message };
    } finally {
      if (context) await cdp.send('Target.disposeBrowserContext', { browserContextId: context });
      writeFileSync(join(binding.out, 'browser-cases.json'), JSON.stringify(results, null, 2) + '\n', { mode: 0o600 });
    }
  }
  results.status = results.cases.every(row => row.status === 'passed') ? 'passed' : 'failed';
  if (results.status !== 'passed') process.exitCode = 1;
} catch (error) {
  results.admissionError = { type: error.name, message: error.message };
  process.exitCode = 1;
} finally {
  writeFileSync(join(binding.out, 'browser-cases.json'), JSON.stringify(results, null, 2) + '\n', { mode: 0o600 });
  try {
    await cdp.send('Browser.close'); results.browserCloseAcknowledged = true;
  } catch (error) {
    results.browserCloseAcknowledged = false;
    results.browserCloseError = { type: error.name, message: error.message };
  } finally {
    cdp.close();
    writeFileSync(join(binding.out, 'browser-cases.json'), JSON.stringify(results, null, 2) + '\n', { mode: 0o600 });
  }
}
