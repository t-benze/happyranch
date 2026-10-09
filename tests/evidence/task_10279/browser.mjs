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
// Fixed physical macOS codes from Chromium's dom_code_data.inc, distinct from Windows VK.
const nativeKeys = binding.platform === 'darwin' ? { Enter: 0x24, Tab: 0x30, Escape: 0x35 } : null;
results.keyboardInput = { platform: binding.platform, nativeKeys, modifiers: 0 };
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
async function keyState(session) {
  return evaluate(session, `({focused:document.hasFocus(), tag:document.activeElement?.tagName || '',
    href:document.activeElement?.getAttribute('href') || '', location:location.pathname, hash:location.hash,
    visibility:document.visibilityState, role:document.activeElement?.getAttribute('role') || ''})`);
}
async function nativeClick(session, selector, label = null) {
  // Observe geometry and hit target; activation itself is real browser input.
  const point = await evaluate(session, `(() => {
    const matches=[...document.querySelectorAll(${JSON.stringify(selector)})]
      .filter(e=>${JSON.stringify(label)}===null || e.textContent.trim()===${JSON.stringify(label)});
    if(matches.length!==1)throw new Error('ambiguous native click target');
    const e=matches[0], r=e.getBoundingClientRect(), x=r.left+r.width/2, y=r.top+r.height/2;
    const hit=document.elementFromPoint(x,y);
    return {x,y,width:r.width,height:r.height,inside:x>=0&&y>=0&&x<innerWidth&&y<innerHeight,
      reachable:!!hit&&(hit===e||e.contains(hit)),disabled:!!e.disabled};
  })()`);
  assert.ok(point.width > 0 && point.height > 0 && point.inside && point.reachable && !point.disabled,
    'native click requires a visible unobscured enabled target');
  for (const type of ['mousePressed', 'mouseReleased'])
    await cdp.send('Input.dispatchMouseEvent', { type, x:point.x, y:point.y, button:'left',
      buttons:type === 'mousePressed' ? 1 : 0, clickCount:1, modifiers:0 }, session);
  await sleep(50);
  return { ...point, after: await keyState(session) };
}
async function key(session, name, code, diagnostics) {
  assert.ok(['Enter', 'Tab', 'Escape'].includes(name));
  const native = nativeKeys ? { nativeVirtualKeyCode: nativeKeys[name] } : {};
  const text = name === 'Enter' ? '\r' : '';
  if (diagnostics) diagnostics.beforeDown = await keyState(session);
  await cdp.send('Input.dispatchKeyEvent', { type: text ? 'keyDown' : 'rawKeyDown', key: name, code: name,
    modifiers: 0, windowsVirtualKeyCode: code, ...native, ...(text ? { text, unmodifiedText: text } : {}) }, session);
  if (diagnostics) diagnostics.afterDown = await keyState(session);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code: name,
    modifiers: 0, windowsVirtualKeyCode: code, ...native }, session);
  // Bound input settling without depending on headless animation-frame delivery.
  await sleep(50);
  if (diagnostics) diagnostics.afterUp = await keyState(session);
}
async function discardCapacityNavigation(session, locale, destination) {
  assert.ok(['/orgs/test/settings/preferences', '/orgs/test/tasks'].includes(destination));
  assert.ok(['en', 'zh-CN'].includes(locale));
  const target = JSON.stringify(destination);
  await wait(session, `location.pathname===${target} || !!document.querySelector('[role="dialog"]')`);
  const observation = { discarded: false, before: await keyState(session),
    visibility: await evaluate(session, 'document.visibilityState') };
  if (await evaluate(session, `location.pathname===${target}`)) return observation;
  const title = locale === 'en' ? 'Discard unsaved capacity changes?' : '放弃未保存的容量更改？';
  const label = locale === 'en' ? 'Discard and continue' : '放弃并继续';
  const ready = `(() => { const dialogs=[...document.querySelectorAll('[role="dialog"]')];
    return dialogs.length===1 && [...dialogs[0].querySelectorAll('h2')].some(e=>e.textContent.trim()===${JSON.stringify(title)})
      && [...dialogs[0].querySelectorAll('button')].filter(e=>e.textContent.trim()===${JSON.stringify(label)} && !e.disabled).length===1; })()`;
  await wait(session, ready);
  const focused = `document.activeElement?.tagName==='BUTTON' && !!document.activeElement.closest('[role="dialog"]')
    && document.activeElement.textContent.trim()===${JSON.stringify(label)}`;
  let reached = false;
  for (let attempt = 0; attempt < 8; attempt++) {
    if (await evaluate(session, focused)) { reached = true; break; }
    await key(session, 'Tab', 9);
  }
  assert.ok(reached, 'real Capacity discard button must be reachable by Tab');
  observation.enter = {};
  await key(session, 'Enter', 13, observation.enter);
  await wait(session, `location.pathname===${target} && !document.querySelector('[role="dialog"]')`);
  observation.discarded = true;
  observation.after = await keyState(session);
  return observation;
}
async function nativeKeyboardProbe() {
  const probe = { scope: 'fixed script-free browser control document; not application evidence', status: 'failed' };
  results.nativeKeyboardProbe = probe;
  let context;
  try {
    context = (await cdp.send('Target.createBrowserContext')).browserContextId;
    const targetId = (await cdp.send('Target.createTarget', { url: 'about:blank', browserContextId: context })).targetId;
    const session = (await cdp.send('Target.attachToTarget', { targetId, flatten: true })).sessionId;
    await cdp.send('Page.enable', {}, session);
    await cdp.send('Runtime.enable', {}, session);
    await cdp.send('Page.bringToFront', {}, session);
    const html = '<!doctype html><meta charset="utf-8"><title>Native keyboard probe</title><a href="#entered">Enter</a><button type="button">Next</button>';
    const frameId = (await cdp.send('Page.getFrameTree', {}, session)).frameTree.frame.id;
    await cdp.send('Page.setDocumentContent', { frameId, html }, session);
    await wait(session, `document.hasFocus() && !!document.querySelector('a[href="#entered"]')`);
    probe.tabs = [];
    for (let attempt = 0; attempt < 3; attempt++) {
      const diagnostic = {}; probe.tabs.push(diagnostic);
      await key(session, 'Tab', 9, diagnostic);
      if (await evaluate(session, `document.activeElement?.getAttribute('href')==='#entered'`)) break;
    }
    assert.equal((await keyState(session)).href, '#entered', 'native probe anchor must be reached by Tab');
    probe.enter = {};
    await key(session, 'Enter', 13, probe.enter);
    await wait(session, `location.hash==='#entered'`);
    probe.status = 'passed';
  } catch (error) {
    probe.error = { type: error.name, message: error.message };
  } finally {
    if (context) await cdp.send('Target.disposeBrowserContext', { browserContextId: context });
  }
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

// Only native input and DOM observations of the ordinary application.
// No synthetic event dispatch, app imports, handler calls or event listeners.
async function fixedShortcut(session, name) {
  assert.ok(['Meta-K', 'Control-K', 'Help'].includes(name));
  const help = name === 'Help';
  const params = { key: help ? '?' : 'k', code: help ? 'Slash' : 'KeyK',
    modifiers: help ? 8 : name === 'Meta-K' ? 4 : 2,
    windowsVirtualKeyCode: help ? 191 : 75,
    ...(binding.platform === 'darwin' ? { nativeVirtualKeyCode: help ? 0x2c : 0x28 } : {}) };
  await cdp.send('Input.dispatchKeyEvent', { type: help ? 'keyDown' : 'rawKeyDown', ...params,
    ...(help ? { text: '?', unmodifiedText: '/' } : {}) }, session);
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...params }, session);
  await sleep(50);
  return { name, params, after: await keyState(session) };
}

async function shellPaletteHelp(session, locale, observation) {
  await cdp.send('Page.bringToFront', {}, session);
  await wait(session, 'document.hasFocus()');
  const editable = `!!document.activeElement?.closest('input,textarea,[contenteditable="true"],[role="textbox"]')`;
  observation.focusTabs = [];
  for (let attempt = 0; attempt < 60 && await evaluate(session, editable); attempt++) {
    await key(session, 'Tab', 9);
    observation.focusTabs.push(await keyState(session));
  }
  const before = await keyState(session);
  observation.before = before;
  assert.equal(await evaluate(session, editable), false, 'shortcuts require actual noneditable focus');
  assert.equal(await evaluate(session, '!!document.querySelector("[role=dialog]")'), false);
  for (const name of ['Meta-K', 'Control-K']) {
    const input = await fixedShortcut(session, name);
    input.dialogCount = await evaluate(session, 'document.querySelectorAll("[role=dialog]").length');
    input.absence = await evaluate(session, absence);
    observation.unbound.push(input);
    assert.equal(input.dialogCount, 0, `${name} remains unbound`);
    assert.deepEqual(input.absence.forbidden, []);
    assert.equal(input.absence.dockCount, 0);
  }
  observation.helpInput = await fixedShortcut(session, 'Help');
  const title = locale === 'en' ? 'Keyboard shortcuts' : '键盘快捷键';
  await wait(session, `(() => { const d=document.querySelector('[role="dialog"]');
    return !!d && [...d.querySelectorAll('h2')].some(e=>e.textContent.trim()===${JSON.stringify(title)}); })()`);
  const labels = await evaluate(session, `[...document.querySelectorAll('[role="dialog"] [role="tab"]')].map(e=>e.textContent.trim())`);
  observation.labels = labels;
  assert.equal(labels.length, 7, 'all surviving help sections are present');
  for (const label of labels) {
    const tabObservation = { label, status:'failed' };
    observation.helpTabs.push(tabObservation);
    tabObservation.input = await nativeClick(session, '[role="dialog"] [role="tab"]', label);
    await wait(session, `!![...document.querySelectorAll('[role="dialog"] [role="tab"]')]
      .find(e=>e.textContent.trim()===${JSON.stringify(label)} && e.getAttribute('aria-selected')==='true')`);
    const tab = await evaluate(session, `(() => { const d=document.querySelector('[role="dialog"]');
      return {text:d.innerText, absence:${absence}}; })()`);
    assert.ok(tab.text.length > 0 && tab.text.length <= 8192, 'bounded readable help');
    assert.ok(!/assistant|助手|a-mode/i.test(tab.text), 'help contains no retired entry');
    assert.deepEqual(tab.absence.forbidden, []);
    assert.equal(tab.absence.dockCount, 0);
    Object.assign(tabObservation, tab, { status:'passed' });
    writeFileSync(join(binding.out, 'browser-cases.json'), JSON.stringify(results, null, 2) + '\n', { mode:0o600 });
  }
  await key(session, 'Escape', 27);
  await wait(session, '!document.querySelector("[role=dialog]")');
  observation.shell = await evaluate(session, absence);
  assert.deepEqual(observation.shell.forbidden, []);
  assert.equal(observation.shell.dockCount, 0);
  return observation;
}

const layoutObservation = String.raw`(() => {
  const root=document.documentElement;
  const alerts=[...document.querySelectorAll('[role="alert"]')].map(e=>e.innerText.slice(0,1024));
  const outside=[...document.querySelectorAll('aside a,main button,main input,main h1,main h2')]
    .filter(e=>{const r=e.getBoundingClientRect();return r.width>0 && (r.left<0 || r.right>innerWidth);})
    .slice(0,30).map(e=>({tag:e.tagName,text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,80),
      left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right}));
  const internalScrollers=[...document.querySelectorAll('main *,aside *')]
    .filter(e=>e.clientWidth>0 && e.scrollWidth>e.clientWidth+1)
    .slice(0,30).map(e=>({tag:e.tagName,role:e.getAttribute('role')||'',
      width:e.clientWidth,scrollWidth:e.scrollWidth,overflowX:getComputedStyle(e).overflowX}));
  return {viewport:innerWidth,scrollWidth:root.scrollWidth,overflow:root.scrollWidth>innerWidth,internalScrollers,
    outside,alerts,knownRawDiagnostics:(document.body.innerText.match(/(?:authority_reviewer_incoherent|profile_dependency_incoherent|Traceback|[a-z_]+_not_registered)/g)||[]).slice(0,20)};
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
  await nativeKeyboardProbe();
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
      await cdp.send('Page.bringToFront', {}, session);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, session);
      await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source:
        `if (location.origin===${JSON.stringify(origin.origin)}) localStorage.setItem('happyranch.ui.locale', ${JSON.stringify(locale)});` }, session);
      row.phase = 'settings-navigation';
      await cdp.send('Page.navigate', { url: binding.base + 'orgs/test/settings/daemon-capacity' }, session);
      await wait(session, `document.documentElement?.lang===${JSON.stringify(locale)} && !!document.querySelector('a[href="/orgs/test/settings/preferences"]')`);
      await wait(session, `!!document.body && !document.body.innerText.includes(${JSON.stringify(locale === 'en' ? 'Loading settings' : '正在加载设置')})`);
      row.phase = 'settings-observation';
      row.settings = await evaluate(session, absence);
      row.settingsLayout = await evaluate(session, layoutObservation);
      assert.deepEqual(row.settings.forbidden, []);
      assert.equal(row.settings.dockCount, 0);
      assert.ok(row.settings.navLinks.some(link => link.href === '/orgs/test/settings/preferences'));
      row.screenshots.push(await screenshot(session, `${locale}-${width}-settings.png`));
      // Focus via native Tab events, then activate the actual Preferences link with Enter.
      // Keep a failed keyboard observation while continuing independent WS/Tasks/HTTP evidence.
      try {
        let reached = false;
        row.phase = 'keyboard-navigation';
        row.keyboardDiagnostics = { beforeTab: await evaluate(session, `({focused:document.hasFocus(),
          tag:document.activeElement?.tagName || '', href:document.activeElement?.getAttribute('href') || '',
          location:location.pathname})`) };
        await wait(session, `document.hasFocus()`);
        for (let attempt = 0; attempt < 60; attempt++) {
          await key(session, 'Tab', 9);
          if (await evaluate(session, `document.activeElement?.getAttribute('href')==='/orgs/test/settings/preferences'`)) {
            reached = true; break;
          }
        }
        assert.ok(reached, 'Preferences link must be reachable by Tab');
        row.keyboardDiagnostics.beforeEnter = await evaluate(session, `({focused:document.hasFocus(),
          tag:document.activeElement?.tagName || '', href:document.activeElement?.getAttribute('href') || '',
          location:location.pathname})`);
        row.keyboardDiagnostics.enterEvents = {};
        await key(session, 'Enter', 13, row.keyboardDiagnostics.enterEvents);
        row.keyboardDiagnostics.afterEnter = await evaluate(session, `({focused:document.hasFocus(),
          tag:document.activeElement?.tagName || '', href:document.activeElement?.getAttribute('href') || '',
          location:location.pathname})`);
        row.keyboardDiagnostics.capacityNavigation = await discardCapacityNavigation(session, locale, '/orgs/test/settings/preferences');
        await wait(session, `location.pathname==='/orgs/test/settings/preferences'`);
        row.keyboard = { tabReachedPreferences: reached, enterNavigated: true };
        await key(session, 'Escape', 27);
        row.preferences = await evaluate(session, absence);
        assert.deepEqual(row.preferences.forbidden, []);
        assert.equal(row.preferences.dockCount, 0);
      } catch (error) {
        row.keyboardError = { type: error.name, message: error.message };
      }
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
      try {
        row.tasksBeforeClick = await keyState(session);
        row.tasksNativeClick = await nativeClick(session, 'a[href="/orgs/test/tasks"]');
        row.tasksAfterClick = await keyState(session);
        row.tasksCapacityNavigation = await discardCapacityNavigation(session, locale, '/orgs/test/tasks');
        await wait(session, `location.pathname==='/orgs/test/tasks' && !!document.querySelector('aside')`);
      } catch (error) {
        row.navigationError = { type: error.name, message: error.message };
      }
      row.tasksAfterWait = await evaluate(session, `({location:location.pathname,
        hasAside:!!document.querySelector('aside'), hasTasksLink:!!document.querySelector('a[href="/orgs/test/tasks"]')})`);
      row.navigation = await evaluate(session, absence);
      row.tasksLayout = await evaluate(session, layoutObservation);
      assert.deepEqual(row.navigation.forbidden, []);
      assert.equal(row.navigation.dockCount, 0);
      row.screenshots.push(await screenshot(session, `${locale}-${width}-${row.navigationError ? 'navigation-failed' : 'tasks'}.png`));
      row.shellPaletteHelp = { unbound: [], helpTabs: [] };
      try {
        await shellPaletteHelp(session, locale, row.shellPaletteHelp);
      } catch (error) {
        row.shellPaletteHelpError = { type: error.name, message: error.message };
        row.shellPaletteHelp.failureState = await evaluate(session, `({
          focus:{tag:document.activeElement?.tagName||'',role:document.activeElement?.getAttribute('role')||'',
            editable:!!document.activeElement?.closest('input,textarea,[contenteditable="true"],[role="textbox"]')},
          focused:document.hasFocus(),visibility:document.visibilityState,location:location.pathname,
          dialogs:[...document.querySelectorAll('[role="dialog"]')].slice(0,3).map(d=>({
            titles:[...d.querySelectorAll('h2')].map(e=>e.textContent.slice(0,120)),
            tabs:[...d.querySelectorAll('[role="tab"]')].slice(0,10).map(e=>({
              label:e.textContent.slice(0,120),selected:e.getAttribute('aria-selected')}))}))
        })`);
      }
      row.phase = 'http-observation';
      row.http = cdp.events.filter(event => event.session === session).map(({ session: _session, ...event }) => event);
      for (const path of ['/api/v1/auth/bootstrap', '/api/v1/orgs', '/api/v1/orgs/test/settings'])
        assert.ok(row.http.some(event => event.path === path && event.status === 200), `real HTTP ${path}`);
      assert.ok(!row.http.some(event => event.path.startsWith('/api/v1/assistant')), 'UI must not call retired Assistant HTTP');
      assert.equal(row.screenshots.length, 2);
      assert.ok(row.screenshots.every(shot => shot.width === width && shot.height === height));
      row.phase = row.keyboardError || row.navigationError || row.shellPaletteHelpError ? 'complete-with-required-check-failure' : 'complete';
      row.status = row.keyboardError || row.navigationError || row.shellPaletteHelpError ? 'failed' : 'passed';
    } catch (error) {
      row.error = { type: error.name, message: error.message };
    } finally {
      if (context) await cdp.send('Target.disposeBrowserContext', { browserContextId: context });
      writeFileSync(join(binding.out, 'browser-cases.json'), JSON.stringify(results, null, 2) + '\n', { mode: 0o600 });
    }
  }
  results.status = results.nativeKeyboardProbe.status === 'passed' &&
    results.cases.every(row => row.status === 'passed') ? 'passed' : 'failed';
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
