/** THR-118 mounted dock cases for the existing ordinary-build CDP harness.
 * Synthetic daemon at the external HTTP/WS boundary; shipping providers,
 * request functions, socket opener and mounted components are unmodified.
 */
import { createHash } from 'node:crypto';

const ROOT = '/api/v1/assistant';
const RAW_TITLE = 'Raw «会话» / original';
const RAW_EXECUTOR = 'executor/原始';
const HISTORY = [{ prompt: 'Raw prompt /任务', started_at: '2026-07-04T10:00:00Z', frames: [
  { type: 'turn_start' }, { type: 'text_delta', text: '**Raw reply** /答复' }, { type: 'turn_end' },
] }];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const json = JSON.stringify;

/** Minimal RFC6455 fixture (no dependency or application transport replacement). */
function writeFrame(socket, value, opcode = 1) {
  const body = Buffer.from(typeof value === 'string' ? value : json(value));
  const header = body.length < 126 ? Buffer.from([0x80 | opcode, body.length])
    : Buffer.from([0x80 | opcode, 126, body.length >> 8, body.length & 255]);
  socket.write(Buffer.concat([header, body]));
}

export function assistantFixture(ledger, hung) {
  let mode = 'empty';
  let conversations = [];
  let nextId = 0;
  let holdWrites = false;
  const sockets = new Map();
  const writes = [];
  const initial = () => [
    { id: 'raw-id', title: RAW_TITLE, active: true, created_at: '2026-07-04T10:00:00Z' },
    { id: 'older-id', title: 'Older / 原始', active: false, created_at: '2026-07-03T10:00:00Z' },
  ];
  function set(next) { mode = next; conversations = initial(); }
  function frames(...values) { for (const { socket } of sockets.values()) for (const value of values) writeFrame(socket, value); }
  function finishWrites() { holdWrites = false; for (const finish of writes.splice(0)) finish(); }
  function attach(server) {
    server.on('upgrade', (request, socket, initialData) => {
      if (request.url !== `${ROOT}/a-mode`) { socket.destroy(); return; }
      const id = ++nextId;
      const accept = createHash('sha1').update(request.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
      // Echo the fixture bearer subprotocol used by the REAL shipping opener.
      const protocol = String(request.headers['sec-websocket-protocol']).split(',')[0].trim();
      socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\nSec-WebSocket-Protocol: ${protocol}\r\n\r\n`);
      ledger.push({ event: 'socket-open', id, mode, t: Date.now() });
      const entry = { socket, buffer: Buffer.alloc(0) }; sockets.set(id, entry);
      const read = chunk => {
        entry.buffer = Buffer.concat([entry.buffer, chunk]);
        while (entry.buffer.length >= 2) {
          const b = entry.buffer;
          const opcode = b[0] & 15, masked = Boolean(b[1] & 128);
          let length = b[1] & 127, offset = 2;
          if (length === 126) { if (b.length < 4) return; length = b.readUInt16BE(2); offset = 4; }
          if (length === 127) { if (b.length < 10) return; length = Number(b.readBigUInt64BE(2)); offset = 10; }
          const mask = masked ? b.subarray(offset, offset + 4) : null;
          if (masked) offset += 4;
          if (b.length < offset + length) return;
          const payload = Buffer.from(b.subarray(offset, offset + length));
          if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];
          entry.buffer = b.subarray(offset + length);
          if (opcode === 8) { socket.end(Buffer.from([0x88, 0])); return; }
          if (opcode === 1) ledger.push({ event: 'client-frame', id, payload: JSON.parse(payload.toString()), t: Date.now() });
        }
      };
      socket.on('data', read); if (initialData.length) read(initialData);
      socket.on('error', () => {});
      socket.on('close', () => { sockets.delete(id); ledger.push({ event: 'socket-close', id, t: Date.now() }); });
      setTimeout(() => {
        if (socket.destroyed || mode === 'connecting') return;
        if (['history', 'tool', 'fallback', 'raw'].includes(mode)) writeFrame(socket, { type: 'history', turns: HISTORY });
        writeFrame(socket, { type: 'status', code: 'ready' });
        if (mode === 'tool') {
          writeFrame(socket, { type: 'turn_start' });
          writeFrame(socket, { type: 'tool_call', name: 'raw_tool/工具' });
          writeFrame(socket, { type: 'tool_result', name: 'raw_tool/工具', ok: false });
        }
        if (mode === 'fallback') writeFrame(socket, { type: 'status', code: 'error' });
        if (mode === 'raw') writeFrame(socket, { type: 'error', message: 'Assistant error.' });
      }, 50);
    });
  }
  function handle(request, response, pathname) {
    if (!pathname.startsWith(ROOT)) return false;
    const reply = (payload, status = 200) => { response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(json(payload)); };
    if (pathname === `${ROOT}/status`) {
      if (mode === 'status-loading') { hung.push(response); return true; }
      if (mode === 'status-error') { reply({ detail: 'Raw status diagnostic' }, 503); return true; }
      reply({ state: mode === 'unconfigured' ? 'unconfigured' : 'configured', selected_executor: RAW_EXECUTOR, workspace_path: '/raw/workspace', detail: null }); return true;
    }
    if (request.method === 'GET') {
      if (mode === 'list-loading') hung.push(response);
      else if (mode === 'list-error') reply({ detail: 'Raw list diagnostic' }, 503);
      else reply(mode === 'list-empty' ? [] : conversations);
      return true;
    }
    let body = '';
    request.on('data', chunk => { body += chunk; });
    request.on('end', () => {
      ledger.push({ event: 'mutation-body', method: request.method, path: pathname, body, t: Date.now() });
      const finish = () => {
        if (request.method === 'POST' && pathname.endsWith('/conversations')) {
          conversations.forEach(c => { c.active = false; });
          const conv = { id: 'new-id', title: 'New raw /会话', created_at: '2026-07-05T10:00:00Z', active: true };
          conversations.unshift(conv); reply(conv);
        } else if (request.method === 'POST') {
          const id = pathname.split('/').at(-2); conversations.forEach(c => { c.active = c.id === id; }); reply({ success: true });
        } else if (request.method === 'PATCH') {
          conversations.find(c => c.id === pathname.split('/').at(-1)).title = JSON.parse(body).title; reply({ success: true });
        } else {
          conversations = conversations.filter(c => c.id !== pathname.split('/').at(-1));
          if (!conversations.some(c => c.active) && conversations.length) conversations[0].active = true;
          reply({ success: true });
        }
      };
      if (holdWrites) writes.push(finish); else finish();
    });
    return true;
  }
  return { set, attach, handle, frames, finishWrites, holdWrites: () => { holdWrites = true; }, cleanup: () => { finishWrites(); for (const { socket } of sockets.values()) socket.destroy(); }, sockets: () => sockets.size };
}

export async function runAssistantCases(h, fixture) {
  const { openPage, closePage, evaluate: ev, waitTrue, clickSrc, capture, check, beginCase, endCase, crossTabSwitch, cdp, ledger, base, tr, seedLocale, chineseNavigator, switchOnly = false } = h;
  const dock = `document.querySelector('[role="dialog"]')`;
  const btn = label => `[...(${dock}).querySelectorAll('button')].find(b=>b.getAttribute('aria-label')===${json(label)} || b.textContent.trim()===${json(label)})`;
  const has = text => `(${dock}).textContent.includes(${json(text)})`;
  const input = `(${dock}).querySelector('textarea')`;
  const rowInput = `(${dock}).querySelector('input')`;
  async function click(page, key, locale = 'en', params) { await clickSrc(page, btn(tr(locale, `assistantDock.${key}`, params))); }
  async function create(mode, locale = 'en', width = 1440, saved = true) {
    fixture.set(mode);
    const page = await openPage(`${base}/orgs/test-org/kb`, { init: `${saved ? seedLocale(locale) : 'localStorage.removeItem("happyranch.ui.locale");'}\n${chineseNavigator}`, width, height: width === 390 ? 844 : 900 });
    if (!await waitTrue(page, `Boolean(document.querySelector('[data-assistant-open]'))`, 'actual shell assistant trigger')) throw new Error('missing assistant trigger');
    await clickSrc(page, `document.querySelector('[data-assistant-open]')`);
    const expected = ['status-loading', 'unconfigured', 'status-error'].includes(mode) ? 'loading' : 'composer';
    if (!await waitTrue(page, expected === 'loading' ? `${dock} && !(${dock}).className.includes('translate-x-full')` : `Boolean(${input})`, 'open dock')) throw new Error('dock did not open');
    if (!['status-loading', 'unconfigured', 'status-error'].includes(mode)) {
      if (!await waitTrue(page, `(${dock}).querySelector('textarea') !== null`, 'configured composer')) throw new Error('not configured');
      await sleep(150);
    }
    return page;
  }
  async function geometry(page, label) {
    // The open class commits before the 200ms sliding transition finishes.
    // Await the observable final position for every view, including views
    // without a composer, then keep the same strict containment assertions.
    check(`${label} open transition settled`, await waitTrue(page, `(() => { const d=${dock}; if (!d) return false; const r=d.getBoundingClientRect(); return d.getAnimations().every(a=>a.playState!=='running') && r.width>0 && r.left>=-1 && r.right<=innerWidth+1; })()`, 'dock opening transition'), true);
    const result = await ev(page, `(() => { const d=${dock}, r=d.getBoundingClientRect(); const essential=[...d.querySelectorAll('button,input,textarea')].filter(e=>!e.disabled); return { dock: !!d && r.width>0 && r.left>=-1 && r.right<=innerWidth+1, contained:d.scrollWidth<=d.clientWidth+1, controls:essential.length>0 && essential.every(e=>{const b=e.getBoundingClientRect(); return b.width>0 && b.left>=r.left-1 && b.right<=r.right+1;}) }; })()`);
    check(`${label} contained dock and essential controls`, result, { dock: true, contained: true, controls: true });
  }
  const views = [
    ['status-loading', ['loading']], ['unconfigured', ['notReady', 'notConfigured']], ['status-error', ['statusError', 'notConfigured']],
    ['connecting', ['composer', 'loadingLabel']], ['empty', ['empty', 'composer']], ['history', ['composer', 'you']],
    ['tool', ['composer', 'toolActivity']], ['fallback', ['assistantError']], ['raw', ['composer']],
    ['list-loading', ['loadingConversations']], ['list-error', ['conversationError']], ['list-empty', ['noConversations']],
    ['rename', ['conversationTitle', 'saveTitle', 'cancelRename']], ['delete', ['deleteConfirm', 'delete', 'cancel']],
  ];
  if (!switchOnly) {
  beginCase('A1-A2-A6', 'ordinary mounted en/zh-CN dock and conversation states at 390/1440');
  for (const locale of ['en', 'zh-CN']) for (const width of [390, 1440]) for (const [state, keys] of views) {
    const page = await create(['rename','delete'].includes(state) ? 'history' : state, locale, width);
    if (state.startsWith('list-') || ['rename','delete'].includes(state)) {
      await click(page, 'conversations', locale);
      if (state === 'rename') await click(page, 'renameTitle', locale, { title: RAW_TITLE });
      if (state === 'delete') await click(page, 'deleteTitle', locale, { title: RAW_TITLE });
    }
    for (const key of keys) {
      const value = tr(locale, `assistantDock.${key}`, { title: RAW_TITLE });
      const expression = key === 'composer' || key === 'loadingLabel' || key === 'toolActivity' || key === 'conversationTitle' || key === 'saveTitle' || key === 'cancelRename'
        ? `Boolean((${dock}).querySelector('[aria-label='+${json(JSON.stringify(value))}+']'))` : has(value);
      check(`${state}/${locale}/${width} ${key}`, await waitTrue(page, expression, `${state} ${key}`), true);
    }
    check(`${state}/${locale}/${width} title localized`, await ev(page, `(${dock}).getAttribute('aria-label')`), tr(locale, 'assistantDock.title'));
    if (['history','tool','fallback','raw'].includes(state)) for (const raw of ['Raw prompt /任务','Raw reply',RAW_EXECUTOR]) check(`${state} raw ${raw}`, await ev(page, has(raw)), true);
    if (state === 'raw') check('raw catalog-equal message', await ev(page, `(${dock}).querySelector('[role="alert"]').textContent`), 'Assistant error.');
    await geometry(page, `${state}/${locale}/${width}`);
    await capture(page, `${locale}-${state}-${width}`, { locale, viewport: width, state, source: 'ordinary built SPA, real HTTP/WS boundaries' });
    await closePage(page); await sleep(50);
  }
  endCase();
  }

  beginCase('A3-A4', 'mounted history/inflight/draft and rename/delete survive locale switches with a real HTTP/socket ledger');
  const page = await create('history');
  check('positive shipping transport evidence: one live socket',fixture.sockets(),1);
  fixture.frames({ type: 'turn_start' }, { type: 'tool_call', name: 'raw_tool/工具' });
  await waitTrue(page, has('raw_tool/工具'), 'inflight tool');
  await ev(page, `(${input}).focus()`); await cdp.send('Input.insertText', { text: '  Original /发送  ' }, page.sessionId);
  await ev(page, `(()=>{(${input}).setSelectionRange(2,10);window.__dock=${dock};window.__composer=${input};return true})()`);
  for (const locale of ['zh-CN','en']) {
    const before = ledger.length;
    await crossTabSwitch(page, locale);
    check(`draft ${locale} nodes/focus/value/selection`, await ev(page, `(()=>{const c=${input};return {dock:window.__dock===${dock},composer:window.__composer===c,focused:document.activeElement===c,value:c.value,selection:[c.selectionStart,c.selectionEnd],path:location.pathname}})()`), { dock:true,composer:true,focused:true,value:'  Original /发送  ',selection:[2,10],path:'/orgs/test-org/kb' });
    check(`draft ${locale} localized caption`, await ev(page, has(tr(locale,'assistantDock.replyingCaption',{elapsed:''}).trim())), true);
    check(`draft ${locale} no HTTP/session/socket activity`, ledger.slice(before), []);
  }
  await cdp.send('Input.dispatchKeyEvent', { type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,modifiers:8 },page.sessionId);
  await cdp.send('Input.dispatchKeyEvent', { type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13 },page.sessionId);
  check('Shift+Enter sends no frame', ledger.filter(r=>r.event==='client-frame'), []);
  // Remove only the browser-entered newline using a real select/type action.
  await ev(page, `(${input}).select()`); await cdp.send('Input.insertText',{text:'  Original /发送  '},page.sessionId);
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13},page.sessionId);
  await sleep(100);
  check('normal Enter sends original trimmed payload once', ledger.filter(r=>r.event==='client-frame').map(r=>r.payload), [{type:'start',text:'Original /发送'}]);
  check('normal send clears composer', await ev(page, `(${input}).value`), '');
  fixture.frames({type:'turn_end'});
  await click(page,'conversations'); await click(page,'renameTitle','en',{title:RAW_TITLE});
  await ev(page, `(${rowInput}).select()`); await cdp.send('Input.insertText',{text:'  Raw /新题  '},page.sessionId);
  await ev(page, `(()=>{(${rowInput}).setSelectionRange(2,8);window.__rename=${rowInput};return true})()`);
  for (const locale of ['zh-CN','en']) {
    const before=ledger.length; await crossTabSwitch(page,locale);
    check(`rename ${locale} node/focus/draft/selection`,await ev(page,`(()=>{const c=${rowInput};return [c===window.__rename,document.activeElement===c,c.value,c.selectionStart,c.selectionEnd]})()`),[true,true,'  Raw /新题  ',2,8]);
    check(`rename ${locale} no HTTP/socket activity`,ledger.slice(before),[]);
  }
  fixture.holdWrites(); await click(page,'saveTitle');
  await waitTrue(page,`(${btn('New conversation')}).disabled`,'mutation busy');
  check('busy disables new/list controls',await ev(page,`(${btn('New conversation')}).disabled`),true);
  fixture.finishWrites(); await waitTrue(page,has('Raw /新题'),'rename success');
  check('rename real PATCH payload',ledger.filter(r=>r.event==='mutation-body'&&r.method==='PATCH').map(r=>[r.path,r.body]),[[`${ROOT}/a-mode/conversations/raw-id`,json({title:'Raw /新题'})]]);
  await click(page,'deleteTitle','en',{title:'Raw /新题'});
  await ev(page,`(()=>{window.__confirm=${btn('Delete')};window.__confirm.focus();return true})()`);
  for(const locale of ['zh-CN','en']){
    const before=ledger.length;await crossTabSwitch(page,locale);
    check(`delete ${locale} same confirm and focus`,await ev(page,`window.__confirm===${btn(tr(locale,'assistantDock.delete'))} && document.activeElement===window.__confirm`),true);
    check(`delete ${locale} localized raw-title confirmation`,await ev(page,has(tr(locale,'assistantDock.deleteConfirm',{title:'Raw /新题'}))),true);
    check(`delete ${locale} no HTTP/socket activity`,ledger.slice(before),[]);
  }
  await click(page,'cancel');check('cancel does not delete',ledger.filter(r=>r.method==='DELETE'),[]);
  await click(page,'deleteTitle','en',{title:'Raw /新题'});await click(page,'delete');
  await waitTrue(page,`!(${dock}).textContent.includes('Raw /新题')`,'deleted row');
  check('delete original id once',ledger.filter(r=>r.event==='mutation-body'&&r.method==='DELETE').map(r=>r.path),[`${ROOT}/a-mode/conversations/raw-id`]);
  await click(page,'newConversation');await waitTrue(page,has('New raw /会话'),'new conversation');
  check('new real POST once',ledger.filter(r=>r.event==='mutation-body'&&r.method==='POST'&&r.path.endsWith('/conversations')).map(r=>r.body),['']);
  await click(page,'conversations');await clickSrc(page,btn('Older / 原始'));await sleep(200);
  check('switch real activation original id',ledger.filter(r=>r.event==='mutation-body'&&r.path.endsWith('/activate')).map(r=>r.path),[`${ROOT}/a-mode/conversations/older-id/activate`]);
  await closePage(page);await sleep(50);endCase();

  if (!switchOnly) {
  beginCase('A4', 'already-visible fallback and raw equal/empty diagnostics switch without reconnecting');
  const errorPage=await create('fallback');
  for(const raw of [undefined,'Assistant error.','']){
    fixture.frames(raw===undefined?{type:'status',code:'error'}:{type:'error',message:raw});
    await sleep(100);
    for(const locale of ['zh-CN','en']){
      const before=ledger.length;await crossTabSwitch(errorPage,locale);
      check(`error ${json(raw)} ${locale}`,await ev(errorPage,`(${dock}).querySelector('[role="alert"]').textContent`),raw===undefined?tr(locale,'assistantDock.assistantError'):raw);
      check('error switch no HTTP/socket activity',ledger.slice(before),[]);
    }
  }
  await closePage(errorPage);await sleep(50);endCase();

  beginCase('A5-A6', 'keyboard close/reopen, browser preference persistence and unset Chinese preview');
  const preview=await create('empty','en',1440,false);
  check('actual Chinese navigator',await ev(preview,'[navigator.language,navigator.languages]'),['zh-CN',['zh-CN','zh']]);
  check('unset Chinese preview stays English',await ev(preview,'document.documentElement.lang'),'en');
  await crossTabSwitch(preview,'zh-CN');await geometry(preview,'saved Chinese');
  await ev(preview,`(${input}).focus()`);await cdp.send('Input.insertText',{text:'Navigation /draft'},preview.sessionId);
  await ev(preview,`(()=>{window.__navDock=${dock};window.__navComposer=${input};(${dock}).querySelector('button').focus();return true})()`);
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:8},preview.sessionId);
  check('Shift-Tab traps focus at last enabled control',await ev(preview,`document.activeElement===${btn('发送')}`),true);
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9},preview.sessionId);
  check('Tab wraps to first enabled control',await ev(preview,`document.activeElement===(${dock}).querySelector('button')`),true);
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},preview.sessionId);
  await waitTrue(preview,`(${dock}).className.includes('translate-x-full')`,'Escape close');
  check('Escape closes dock',await ev(preview,`(${dock}).className.includes('translate-x-full')`),true);
  check('close restores focus to actual shell trigger',await ev(preview,`document.activeElement.matches('[data-assistant-open]')`),true);
  await clickSrc(preview,`document.querySelector('a[href="/orgs/test-org/health"]')`);
  await waitTrue(preview,`location.pathname==='/orgs/test-org/health'`,'ordinary SPA route navigation');
  check('SPA navigation preserves mounted dock/composer draft',await ev(preview,`[${dock}===window.__navDock,${input}===window.__navComposer,(${input}).value,location.pathname]`),[true,true,'Navigation /draft','/orgs/test-org/health']);
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'k',code:'KeyK',windowsVirtualKeyCode:75,modifiers:2},preview.sessionId);
  await waitTrue(preview,`!(${dock}).className.includes('translate-x-full')`,'Ctrl-K reopen');
  check('Ctrl-K reopens localized dock',await ev(preview,`(${dock}).getAttribute('aria-label')`),'牧场助手');
  // Stop the initial unset-preference fixture from clearing the user's saved choice on reload.
  await cdp.send('Page.removeScriptToEvaluateOnNewDocument',{identifier:preview.initializationScript},preview.sessionId);
  await cdp.send('Page.addScriptToEvaluateOnNewDocument',{source:chineseNavigator},preview.sessionId);
  const loaded=cdp.waitFor('Page.loadEventFired',{sessionId:preview.sessionId});await cdp.send('Page.reload',{},preview.sessionId);await loaded;
  check('browser saved Chinese survives reload',await ev(preview,'document.documentElement.lang'),'zh-CN');
  await closePage(preview);endCase();
  }
}
