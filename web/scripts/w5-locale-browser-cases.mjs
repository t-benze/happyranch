/** W5b ordinary release activation, using the existing W4a CDP/origin helpers.
 * Document-start observation is external to the app: freeze connected text
 * synchronously at DOM insertion, before effects, and never overwrite it.
 * All navigator/storage fixtures and daemon responses are explicitly test-side.
 */
const KEY = 'happyranch.ui.locale';
const PATH = '/orgs/test-org/settings/preferences';
const radio = locale => `document.querySelector('input[name="happyranch-ui-language"][value="${locale}"]')`;
const state = `({lang:document.documentElement.lang,saved:localStorage.getItem('${KEY}'),checked:document.querySelector('input[name="happyranch-ui-language"]:checked')?.value,writes:window.__w5Writes,events:window.__w5Events,transports:window.__w5Transports})`;
function observe(languages) {
  return `(() => {
    Object.defineProperty(Navigator.prototype,'language',{configurable:true,get:()=>${JSON.stringify(languages[0])}});
    Object.defineProperty(Navigator.prototype,'languages',{configurable:true,get:()=>${JSON.stringify(languages)}});
    window.__w5Writes=[]; window.__w5Events=[]; window.__w5Transports=[];
    const set=Storage.prototype.setItem;
    Storage.prototype.setItem=function(key,value){window.__w5Writes.push({area:this===localStorage?'local':'session',key,value});return set.call(this,key,value)};
    for(const name of ['WebSocket','EventSource']) {const Original=window[name]; if(Original) window[name]=class extends Original {constructor(...args){window.__w5Transports.push({name,url:String(args[0])});super(...args)}}}
    addEventListener('storage',e=>window.__w5Events.push({key:e.key,newValue:e.newValue,area:e.storageArea===localStorage?'local':'session'}));
    function capture(){const root=document.getElementById('root');if(!window.__w5First&&root?.isConnected&&root.textContent.trim())window.__w5First={lang:document.documentElement.lang,text:root.textContent,navigator:{language:navigator.language,languages:[...navigator.languages]}}}
    for(const name of ['appendChild','insertBefore']){const original=Node.prototype[name];Node.prototype[name]=function(...args){const result=original.apply(this,args);capture();return result}}
  })();`;
}
const preferenceWrites = s => s.writes.filter(w => w.area === 'local' && w.key === KEY);

// Browser layout owns this contract: jsdom cannot measure clipping or text ranges.
// Scroll each essential node into view, then compare its rect and text to every
// actual clipping ancestor, including viewport. Return observations, never nodes.
function readableBounds(selector) {
  return `(() => {
    const root = document.querySelector(${JSON.stringify(selector)});
    if (!root) return { missing: true, measurements: [], over: ['missing root'] };
    const rect = r => ({left:r.left,right:r.right,top:r.top,bottom:r.bottom});
    const measurements = [], over = [];
    for (const el of root.querySelectorAll('h2,h3,p,legend,label,input,span,a')) {
      el.scrollIntoView({block:'center',inline:'nearest'});
      const limits = [{name:'viewport',x:true,y:true,left:0,right:innerWidth,top:0,bottom:innerHeight}];
      for (let a = el.parentElement; a; a = a.parentElement) {
        const css = getComputedStyle(a), r = a.getBoundingClientRect();
        const x = /^(hidden|clip|auto|scroll)$/.test(css.overflowX), y = /^(hidden|clip|auto|scroll)$/.test(css.overflowY);
        if (x || y) limits.push({name:a.tagName+':'+a.className,x,y,left:r.left+a.clientLeft,right:r.left+a.clientLeft+a.clientWidth,top:r.top+a.clientTop,bottom:r.top+a.clientTop+a.clientHeight});
      }
      const ranges = [];
      const walker = document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        if (!walker.currentNode.textContent.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(walker.currentNode);
        for (const r of range.getClientRects()) ranges.push(rect(r));
      }
      const r = rect(el.getBoundingClientRect());
      for (const [kind,box] of [['child',r],...ranges.map(box=>['text',box])]) for (const limit of limits) {
        if ((limit.x && (box.left<limit.left-1 || box.right>limit.right+1)) || (limit.y && (box.top<limit.top-1 || box.bottom>limit.bottom+1))) over.push({tag:el.tagName,text:el.textContent,kind,box,limit});
      }
      measurements.push({tag:el.tagName,text:el.textContent,rect:r,ranges,limits});
    }
    return {missing:false,measurements,over};
  })()`;
}


export async function runLocaleActivationCases(h) {
  const { openPage, closePage, evaluate, waitTrue, clickSrc, capture, check, beginCase, endCase, cdp, ledger, base, tr } = h;
  const ready = page => waitTrue(page, `Boolean(${radio('en')})`, 'mounted Preferences');
  async function seed(saved) {
    const page = await openPage(`${base}/__w4a_blank`);
    await evaluate(page, saved === null ? `localStorage.removeItem('${KEY}')` : `localStorage.setItem('${KEY}',${JSON.stringify(saved)})`);
    await closePage(page);
  }
  async function reload(page) {
    const loaded = cdp.waitFor('Page.loadEventFired', { sessionId: page.sessionId });
    await cdp.send('Page.reload', {}, page.sessionId);
    await loaded;
    check('reload renders Preferences', await ready(page), true);
  }
  async function settled(page, locale) {
    check(`mounted tab resolves ${locale}`, await waitTrue(page, `document.documentElement.lang==='${locale}'&&${radio(locale)}?.checked`, `tab ${locale}`), true);
  }

  if (!h.geometryOnly) {
  beginCase('W5-startup', 'ordinary entry: first connected owned text, actual navigator, saved precedence, no startup write');
  const table = [
    ['missing-Chinese', null, ['zh-CN','zh'], 'zh-CN'],
    ['invalid-Chinese', 'invalid', ['zh-TW'], 'zh-CN'],
    ['saved-English-Chinese', 'en', ['zh-CN'], 'en'],
    ['saved-Chinese-English', 'zh-CN', ['en-US'], 'zh-CN'],
    ['English', null, ['en-US'], 'en'],
    ['unsupported', null, ['fr-FR'], 'en'],
    ['ordered-Chinese', null, ['fr-FR','zh-HK','en'], 'zh-CN'],
    ['ordered-English', null, ['en-GB','zh-CN'], 'en'],
  ];
  for (const [name,saved,languages,expected] of table) for (const width of ['missing-Chinese','saved-English-Chinese'].includes(name) ? [390,1440] : [1440]) {
    await seed(saved);
    const page = await openPage(`${base}/?localeStartup=1`, { init: observe(languages), width, height:width===390?844:900 });
    check(`${name}: first connected text exists`, await waitTrue(page, 'Boolean(window.__w5First)', name), true);
    const first = await evaluate(page, 'window.__w5First');
    check(`${name}: actual navigator read-back`, first?.navigator, { language: languages[0], languages });
    check(`${name}: synchronous first html.lang`, first?.lang, expected);
    check(`${name}: first owned loading text`, first?.text.includes(expected === 'en' ? 'Loading…' : '加载中…'), true);
    check(`${name}: no opposite initial loading text`, first?.text.includes(expected === 'en' ? '加载中…' : 'Loading…'), false);
    const current = await evaluate(page, state);
    check(`${name}: no startup locale write`, preferenceWrites(current), []);
    check(`${name}: original saved bytes`, current.saved, saved);
    if (['missing-Chinese','saved-English-Chinese'].includes(name)) await capture(page,`w5-startup-${expected}-${width}`,{locale:expected,viewport:`${width}x${width===390?844:900}`,first});
    await closePage(page);
  }
  endCase();
  if (h.startupOnly) return;

  beginCase('W5-tabs', 'genuine same-origin mounted tabs: user set/change, delete/clear/invalid, ignored key/area, no echo');
  await seed('en');
  const left = await openPage(`${base}${PATH}`, { init: observe(['zh-CN','zh']) });
  const right = await openPage(`${base}${PATH}`, { init: observe(['zh-CN','zh']) });
  check('left mounted', await ready(left), true); check('right mounted', await ready(right), true);
  await new Promise(r => setTimeout(r,600));
  for (const locale of ['zh-CN','en']) {
    const from = ledger.length;
    const before = await evaluate(right,state);
    await clickSrc(left,radio(locale));
    await settled(left,locale); await settled(right,locale);
    await new Promise(r=>setTimeout(r,600));
    const recipient = await evaluate(right,state);
    check(`user ${locale}: recipient observed real storage event`, recipient.events.some(e=>e.key===KEY&&e.newValue===locale&&e.area==='local'), true);
    check(`user ${locale}: recipient no echo`, preferenceWrites(recipient), preferenceWrites(before));
    check(`user ${locale}: saved`, recipient.saved,locale);
    check(`user ${locale}: zero API switch window`,ledger.slice(from),[]);
    check(`user ${locale}: no new transports`,recipient.transports,before.transports);
  }
  for (const [name,expression,key,value] of [
    ['delete',`localStorage.removeItem('${KEY}')`,KEY,null],
    ['clear','localStorage.clear()',null,null],
    ['invalid',`localStorage.setItem('${KEY}','invalid')`,KEY,'invalid'],
  ]) {
    // Reset using the real selector, whose storage write reaches the other tab.
    await clickSrc(left,radio('en'));
    // If already selected, explicitly select Chinese then English to ensure a write.
    if ((await evaluate(right,state)).lang !== 'en') {await clickSrc(left,radio('zh-CN'));await clickSrc(left,radio('en'));}
    await settled(right,'en');
    await evaluate(right,'window.__w5Events=[]');
    const before = await evaluate(right,state); const from = ledger.length;
    await evaluate(left,expression);
    await settled(right,'zh-CN');
    const recipient = await evaluate(right,state);
    check(`${name}: genuine local event`,recipient.events.some(e=>e.key===key&&e.newValue===value&&e.area==='local'),true);
    check(`${name}: no recipient echo`,preferenceWrites(recipient),preferenceWrites(before));
    check(`${name}: zero locale API`,ledger.slice(from),[]);
    // The writing window receives no self-event; its unchanged state is honest.
    check(`${name}: writer has no self-event`,(await evaluate(left,state)).lang,'en');
    await reload(left); await settled(left,'zh-CN');
    check(`${name}: reload performs no preference write`,preferenceWrites(await evaluate(left,state)),[]);
  }
  for (const expression of ["localStorage.setItem('w5.unrelated','en')",`sessionStorage.setItem('${KEY}','en')`]) {
    const before = await evaluate(right,state);
    await evaluate(left,expression); await new Promise(r=>setTimeout(r,200));
    const after = await evaluate(right,state);
    check(`ignored ${expression}: locale remains Chinese`,after.lang,'zh-CN');
    check(`ignored ${expression}: no echo`,preferenceWrites(after),preferenceWrites(before));
  }
  check('unrelated key event actually delivered', (await evaluate(right,state)).events.some(e=>e.key==='w5.unrelated'),true);
  check('separate top-level tab session area has no shared event', (await evaluate(right,state)).events.some(e=>e.area==='session'),false);
  await closePage(left); await closePage(right); endCase();

  }

  beginCase('W5-preferences-geometry-and-reload', 'ordinary en/zh at390/1440: disclosure, CJK, child/text/control bounds, switch identity/focus and reload');
  for (const locale of ['en','zh-CN']) for(const width of [390,1440]) {
    await seed(locale);
    const page=await openPage(`${base}${PATH}`,{init:observe(['zh-CN','zh']),width,height:width===390?844:900});
    check(`${locale}/${width}: Preferences mounted`,await ready(page),true);
    await settled(page,locale);
    check(`${locale}/${width}: first connected lang`,(await evaluate(page,'window.__w5First')).lang,locale);
    check(`${locale}/${width}: bilingual availability disclosure`,await evaluate(page,`document.querySelector('[data-testid="settings-preferences"]').textContent.includes(${JSON.stringify(tr(locale,'settings.preferences.coverageDisclosure'))})`),true);
    const geometry=await evaluate(page,`(() => {
      const panel=document.querySelector('[data-testid="settings-preferences"]');const box=panel.getBoundingClientRect();const over=[];
      const rect=r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom});
      const children=[...panel.querySelectorAll('p,legend,label,input,span')].map(el=>({tag:el.tagName,text:el.textContent,rect:rect(el.getBoundingClientRect())}));
      for(const el of panel.querySelectorAll('p,legend,label,input,span')){const r=el.getBoundingClientRect();if(r.left<box.left-1||r.right>box.right+1||r.left<0||r.right>innerWidth)over.push(el.tagName+':'+el.textContent)}
      const ranges=[];const walker=document.createTreeWalker(panel,NodeFilter.SHOW_TEXT);while(walker.nextNode()){const node=walker.currentNode;if(!node.textContent.trim())continue;const range=document.createRange();range.selectNodeContents(node);for(const r of range.getClientRects()){ranges.push({text:node.textContent,rect:rect(r)});if(r.left<box.left-1||r.right>box.right+1||r.left<0||r.right>innerWidth)over.push('text:'+node.textContent)}}
      return {box:rect(box),children,ranges,over,documentOverflow:document.documentElement.scrollWidth>innerWidth+1,description:panel.querySelector('fieldset').getAttribute('aria-describedby'),live:panel.querySelector('[role="status"]').getAttribute('aria-live')};
    })()`);
    check(`${locale}/${width}: essential child and text ranges contained`,geometry.over,[]);
    check(`${locale}/${width}: no document overflow`,geometry.documentOverflow,false);
    check(`${locale}/${width}: actual child/range measurements`,geometry.children.length>0&&geometry.ranges.length>0,true);
    check(`${locale}/${width}: accessible description/live status`,Boolean(geometry.description)&&geometry.live==='polite',true);
    const clipping = await evaluate(page, readableBounds('[data-testid="settings-content"] main'));
    check(`${locale}/${width}: heading/help/disclosure/endonyms/status inside every clipping ancestor`,clipping.over,[]);
    check(`${locale}/${width}: complete clipping measurements`,clipping.measurements.length>0,true);
    await cdp.send('DOM.enable',{},page.sessionId);await cdp.send('CSS.enable',{},page.sessionId);
    const {root}=await cdp.send('DOM.getDocument',{},page.sessionId);
    const {nodeId}=await cdp.send('DOM.querySelector',{nodeId:root.nodeId,selector:'span[lang="zh-CN"]'},page.sessionId);
    const {fonts}=await cdp.send('CSS.getPlatformFontsForNode',{nodeId},page.sessionId);
    check(`${locale}/${width}: Chinese endonym has platform CJK glyphs`,fonts.some(f=>f.glyphCount>0&&/CJK|Chinese|Han|Hei|Song|WenQuan|PingFang|YaHei/i.test(f.familyName)),true);
    await capture(page,`w5-preferences-${locale}-${width}`,{locale,viewport:`${width}x${width===390?844:900}`,geometry,clipping,fonts});
    await evaluate(page,`window.__w5Panel=document.querySelector('[data-testid="settings-preferences"]');window.__w5En=${radio('en')};window.__w5Zh=${radio('zh-CN')};window.__w5Nav=document.querySelector('[data-testid="settings-content"] aside'); true`);
    await new Promise(r=>setTimeout(r,600));
    for(const next of [locale==='en'?'zh-CN':'en',locale]) {
      const before=await evaluate(page,state);const from=ledger.length;
      await clickSrc(page,radio(next));await settled(page,next);
      await new Promise(r=>setTimeout(r,600));
      check(`${locale}/${width}->${next}: panel/radio identity, focus and route`,await evaluate(page,`document.querySelector('[data-testid="settings-preferences"]')===window.__w5Panel&&${radio('en')}===window.__w5En&&${radio('zh-CN')}===window.__w5Zh&&document.activeElement===${radio(next)}&&document.querySelector('[data-testid="settings-content"] aside')===window.__w5Nav&&${radio(next)}.checked&&location.pathname==='${PATH}'`),true);
      const after = await evaluate(page,state);
      check(`${locale}/${width}->${next}: exactly the explicit user preference write`,preferenceWrites(after).slice(preferenceWrites(before).length),[{area:'local',key:KEY,value:next}]);
      check(`${locale}/${width}->${next}: zero locale API`,ledger.slice(from),[]);
      check(`${locale}/${width}->${next}: no transport start/reconnect`,(await evaluate(page,state)).transports,before.transports);
      check(`${locale}/${width}->${next}: honest durable status`,await evaluate(page,`document.querySelector('[role="status"]').textContent.includes(${JSON.stringify(tr(next,'settings.preferences.status.durable'))})`),true);
      const switchedBounds=await evaluate(page,readableBounds('[data-testid="settings-preferences"]'));
      check(`${locale}/${width}->${next}: full copy and live saved status remain readable`,switchedBounds.over,[]);
    }
    await capture(page,`w5-preferences-status-${locale}-${width}`,{locale,viewport:`${width}x${width===390?844:900}`,state:await evaluate(page,state)});
    // Reach the checked radio through real Tab presses, then activate its peer.
    await evaluate(page,'document.activeElement.blur(); true');
    let reached=false;
    for(let tabs=0;tabs<60;tabs++) {
      await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9},page.sessionId);
      await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9},page.sessionId);
      if(await evaluate(page,`document.activeElement===${radio(locale)}`)){reached=true;break;}
    }
    check(`${locale}/${width}: Tab reaches the checked language control`,reached,true);
    // Real keyboard activation, separate from pointer checks above.
    await evaluate(page,`${radio(locale)}.focus(); true`);
    const key = locale==='en'?'ArrowDown':'ArrowUp', next = locale==='en'?'zh-CN':'en';
    const keyboardFrom=ledger.length;
    await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key,code:key,windowsVirtualKeyCode:key==='ArrowDown'?40:38},page.sessionId);
    await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key,code:key,windowsVirtualKeyCode:key==='ArrowDown'?40:38},page.sessionId);
    await settled(page,next);
    check(`${locale}/${width}: keyboard radio focus and selection`,await evaluate(page,`document.activeElement===${radio(next)}&&${radio(next)}.checked`),true);
    check(`${locale}/${width}: keyboard change has zero HTTP`,ledger.slice(keyboardFrom),[]);
    await clickSrc(page,radio(locale));await settled(page,locale);
    await reload(page);await settled(page,locale);
    check(`${locale}/${width}: reload retains saved choice`,(await evaluate(page,state)).saved,locale);
    check(`${locale}/${width}: reload no automatic write`,preferenceWrites(await evaluate(page,state)),[]);
    await closePage(page);
  }
  endCase();

  if (h.geometryOnly) return;

  beginCase('W5-settings-navigation', 'five existing shared Settings routes and active links at390/1440 in both locales');
  const sections=['daemon-capacity','assistant','organization','executors','preferences'];
  for (const locale of ['en','zh-CN']) for (const width of [390,1440]) {
    await seed(locale);
    const page=await openPage(`${base}${PATH}`,{init:observe(['zh-CN','zh']),width,height:width===390?844:900});
    check(`${locale}/${width}: shared shell mounted`,await ready(page),true);
    for (const section of sections) {
      const href=`/orgs/test-org/settings/${section}`;
      await clickSrc(page,`document.querySelector('[data-testid="settings-content"] aside a[href="${href}"]')`);
      check(`${locale}/${width}/${section}: original route reachable`,await waitTrue(page,`location.pathname==='${href}'&&Boolean(document.querySelector('[data-testid="settings-content"] aside a[aria-current="page"][href="${href}"]'))`,'active Settings route'),true);
      const layout=await evaluate(page,`(() => {const content=document.querySelector('[data-testid="settings-content"]'),nav=content.querySelector('aside').getBoundingClientRect(),panel=content.querySelector('main').getBoundingClientRect();return {stacked:nav.bottom<=panel.top+1,rail:nav.right<=panel.left+1,panelWidth:panel.width};})()`);
      check(`${locale}/${width}/${section}: responsive shared nav/panel arrangement`,width<640?layout.stacked:layout.rail,true);
      check(`${locale}/${width}/${section}: panel has usable width`,layout.panelWidth>250,true);
      const nav=await evaluate(page,`(() => {const links=[...document.querySelectorAll('[data-testid="settings-content"] aside a')];return {hrefs:links.map(a=>a.getAttribute('href')),labels:links.map(a=>a.textContent.trim()),icons:links.every(a=>Boolean(a.querySelector('svg'))),active:links.filter(a=>a.getAttribute('aria-current')==='page').map(a=>a.getAttribute('href')),shell:document.documentElement.lang};})()`);
      check(`${locale}/${width}/${section}: five unchanged links in original order`,nav.hrefs,sections.map(key=>`/orgs/test-org/settings/${key}`));
      check(`${locale}/${width}/${section}: single active link`,nav.active,[href]);
      check(`${locale}/${width}/${section}: unchanged translated labels`,nav.labels,['settings.nav.daemonCapacity','settings.nav.assistant','settings.nav.organization','settings.nav.executors','settings.nav.preferences'].map(key=>tr(locale,key)));
      check(`${locale}/${width}/${section}: icons and shell locale`,nav.icons&&nav.shell===locale,true);
      const bounds=await evaluate(page,readableBounds('[data-testid="settings-content"] aside'));
      check(`${locale}/${width}/${section}: all existing nav child/text bounds`,bounds.over,[]);
      if (section==='preferences') await capture(page,`w5-settings-nav-${locale}-${width}`,{locale,viewport:`${width}x${width===390?844:900}`,nav,bounds});
    }
    await closePage(page);
  }
  endCase();
}
