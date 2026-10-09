/** THR118 seq88: shipping AppBar acceptance using the existing W4a CDP fixture.
 * Run --slice header-language --header-case layout|interaction|draft|all.
 * No production instrumentation, dependency or live daemon is used.
 */
const KEY = 'happyranch.ui.locale';
const PREFS = '/orgs/test-org/settings/preferences';
const header = `document.querySelector('[role="combobox"][aria-label="Language"],[role="combobox"][aria-label="语言"]')`;
const radio = locale => `document.querySelector('input[name="happyranch-ui-language"][value="${locale}"]')`;
const option = locale => `[...document.querySelectorAll('[role="option"]')].find(e => e.textContent.trim() === ${JSON.stringify(locale === 'en' ? 'English' : '简体中文')})`;
const edit = `[...document.querySelectorAll('main button')].find(b => ['Edit system prompt','编辑系统提示词'].includes(b.textContent.trim()))`;
const observeWrites = `window.__headerWrites=[];const headerSet=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(this===localStorage&&k==='${KEY}')window.__headerWrites.push(v);return headerSet.call(this,k,v);};`;

// Independent observable geometry: nonempty control/text boxes, viewport,
// every clipping ancestor on both axes, computed readability, and hit testing.
const bounds = expression => `(() => {
  const el = (${expression}); if (!el) return {missing:true};
  const r = el.getBoundingClientRect(), css = getComputedStyle(el), failures = [];
  const limits = [{name:'viewport',x:true,y:true,left:0,right:innerWidth,top:0,bottom:innerHeight}];
  for(let a=el.parentElement;a;a=a.parentElement){
    const s=getComputedStyle(a),b=a.getBoundingClientRect();
    const x=/^(hidden|clip|auto|scroll)$/.test(s.overflowX),y=/^(hidden|clip|auto|scroll)$/.test(s.overflowY);
    if(x||y)limits.push({name:a.tagName+':'+a.className,x,y,left:b.left+a.clientLeft,right:b.left+a.clientLeft+a.clientWidth,top:b.top+a.clientTop,bottom:b.top+a.clientTop+a.clientHeight});
  }
  const boxes=[{kind:'control',left:r.left,right:r.right,top:r.top,bottom:r.bottom}];
  const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
  while(walker.nextNode())if(walker.currentNode.textContent.trim()){
    const range=document.createRange();range.selectNodeContents(walker.currentNode);
    for(const b of range.getClientRects())boxes.push({kind:'text',left:b.left,right:b.right,top:b.top,bottom:b.bottom});
  }
  for(const box of boxes)for(const limit of limits)if((limit.x&&(box.left<limit.left-1||box.right>limit.right+1))||(limit.y&&(box.top<limit.top-1||box.bottom>limit.bottom+1)))failures.push({box,limit});
  const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
  return {missing:false,boxes,limits,failures,nonempty:r.width>0&&r.height>0,readable:parseFloat(css.fontSize)>=10&&css.visibility==='visible'&&css.opacity!=='0',reachable:hit===el||el.contains(hit)};
})()`;

export async function runHeaderLanguageCases(h) {
  const {openPage,closePage,evaluate,waitTrue,clickSrc:rawClick,capture,check,beginCase,endCase,cdp,ledger,base,tr,seedLocale,chineseNavigator,selectedCase='all'}=h;
  async function bounded(promise,label){
    let timer;
    try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(`header CDP operation timed out: ${label}`)),10000);})]);}
    finally{clearTimeout(timer);}
  }
  const clickSrc=async(page,expression)=>{
    console.log(`  pointer: ${expression}`);
    await bounded(cdp.send('Page.bringToFront',{},page.sessionId),'activate pointer tab');
    await bounded(rawClick(page,expression),`pointer ${expression}`);
  };
  const http=[],transport=[];
  cdp.handlers.set('Network.requestWillBeSent',[m=>http.push({sessionId:m.sessionId,method:m.params.request.method,url:m.params.request.url})]);
  for(const name of ['webSocketCreated','webSocketClosed','webSocketFrameSent','webSocketFrameReceived','eventSourceMessageReceived'])cdp.handlers.set(`Network.${name}`,[m=>transport.push({name,sessionId:m.sessionId,params:m.params})]);
  const pause=()=>new Promise(r=>setTimeout(r,600));
  const ready=page=>waitTrue(page,`Boolean(${header})`,'public header language selector',3000);
  async function key(page,name){
    const codes={Tab:9,Enter:13,Escape:27,ArrowDown:40,ArrowUp:38,Space:32};
    for(const type of ['keyDown','keyUp'])await bounded(cdp.send('Input.dispatchKeyEvent',{type,key:name==='Space'?' ':name,code:name,windowsVirtualKeyCode:codes[name],nativeVirtualKeyCode:codes[name]},page.sessionId),`${type} ${name}`);
  }
  async function choose(page,locale){
    await clickSrc(page,header);
    check(`menu has both native endonyms before choosing ${locale}`,await waitTrue(page,`Boolean(${option('en')})&&Boolean(${option('zh-CN')})`,'open menu'),true);
    await clickSrc(page,option(locale));
    check(`header applies ${locale}`,await waitTrue(page,`document.documentElement.lang==='${locale}'&&${header}.textContent.includes(${JSON.stringify(locale==='en'?'English':'简体中文')})&&!document.querySelector('[role="listbox"]')`,'immediate locale/closed menu'),true);
  }
  const theme = `${header}.parentElement.querySelector('button[title]:not([role="combobox"]):not([data-assistant-open])')`;
  async function geometry(page,expression,label){
    const observation=await evaluate(page,bounds(expression));
    check(`${label}: real rectangles and clipping ancestors`,observation.missing?observation:{missing:observation.missing,failures:observation.failures,nonempty:observation.nonempty,readable:observation.readable,reachable:observation.reachable},{missing:false,failures:[],nonempty:true,readable:true,reachable:true});
    // Retain actual rectangles/text lines and ancestor limits with the check.
    check(`${label}: measured boxes exist`,(observation.boxes?.length||0)>0,true);
    return observation;
  }
  if(['all','layout','default','tasks','layout-proof'].includes(selectedCase)){
    beginCase('H-layout','header/options: both languages, widths, densities and shipping default/tasks presentation');
    for(const route of [PREFS,'/orgs/test-org/tasks']){
      if(selectedCase==='default'&&route!==PREFS||selectedCase==='tasks'&&route===PREFS)continue;
      for(const locale of ['en','zh-CN'])for(const density of ['comfortable','compact'])for(const [width,height] of [[390,844],[1440,900]]){
        if(selectedCase==='layout-proof'&&(route!==PREFS||locale!=='en'||density!=='comfortable'||width!==390))continue;
        console.log(`  opening ${route} ${locale} ${density} ${width}`);
        const page=await bounded(openPage(base+route,{init:`${seedLocale(locale)}\n${chineseNavigator}\nlocalStorage.setItem('happyranch.density','${density}');`,width,height}),`open ${route} ${locale} ${density} ${width}`);
        const exists=await ready(page);check('mounted shipping header exists',exists,true);
        if(!exists){await closePage(page);endCase();return;}
        const prefix=`${route===PREFS?'default':'tasks'}-${locale}-${density}-${width}`;
        if(route!==PREFS)check(`${prefix}: real tasks empty state loaded`,await waitTrue(page,`document.querySelector('main')?.textContent.includes(${JSON.stringify(tr(locale,'tasks.list.emptyTitle'))})`,'valid tasks fixture'),true);
        const controls=`${header}.parentElement`,bar=`${controls}.parentElement`;
        check(`${prefix}: translated name/title and endonym lang`,await evaluate(page,`[${header}.getAttribute('aria-label'),${header}.getAttribute('title'),${header}.querySelector('[lang]').lang]`),[tr(locale,'common.language'),tr(locale,'common.language'),locale]);
        check(`${prefix}: adjacent theme and usable assistant`,await evaluate(page,`${header}.nextElementSibling===${theme}&&${header}.previousElementSibling.hasAttribute('data-assistant-open')`),true);
        const measurements=[];
        for(const [expression,label] of [[header,'language'],[theme,'theme'],[`${header}.previousElementSibling`,'assistant'],[`${bar}.firstElementChild`,'title']])measurements.push(await geometry(page,expression,`${prefix} ${label}`));
        check(`${prefix}: controls do not overlap title`,await evaluate(page,`${bar}.firstElementChild.getBoundingClientRect().right<=${controls}.getBoundingClientRect().left+1`),true);
        await clickSrc(page,header);
        check(`${prefix}: both options rendered`,await waitTrue(page,`Boolean(${option('en')})&&Boolean(${option('zh-CN')})`,'both options'),true);
        for(const value of ['en','zh-CN']){
          check(`${prefix}: ${value} selected`,await evaluate(page,`${option(value)}.getAttribute('aria-selected')`),String(value===locale));
          measurements.push(await geometry(page,option(value),`${prefix} ${value} option`));
        }
        await capture(page,`header-${prefix}`,{locale,density,route,width,height,measurements});
        console.log(`  closing ${prefix}`);await key(page,'Escape');await closePage(page);console.log(`  closed ${prefix}`);
      }
    }
    endCase();
  }
  if(['all','interaction'].includes(selectedCase)){
    beginCase('H-interaction','real keyboard/pointer, theme, header/Settings agreement, reload and tabs');
    // Seed on a disposable document so the real reload cannot rewrite it.
    const seed=await openPage(base+'/__w4a_blank');
    await evaluate(seed,seedLocale('en'));await closePage(seed);
    const page=await openPage(base+PREFS,{init:`${chineseNavigator}\nlocalStorage.setItem('happyranch.theme','light');\n${observeWrites}`});
    const exists=await ready(page);check('shipping combobox exists',exists,true);
    if(!exists){await closePage(page);endCase();return;}
    await waitTrue(page,`Boolean(${radio('en')})`,'mounted Preferences');await pause();
    await evaluate(page,`window.__header=${header};window.__panel=${radio('en')}.closest('fieldset');window.__path=location.pathname`);
    for(let i=0;i<40&&!await evaluate(page,`document.activeElement===${header}`);i++)await key(page,'Tab');
    check('real Tab reaches header',await evaluate(page,`document.activeElement===${header}`),true);
    for(const [openKey,arrow,locale] of [['Enter','ArrowDown','zh-CN'],['Space','ArrowUp','en']]){
      const from=ledger.length,networkFrom=http.length,transportFrom=transport.length;
      await key(page,openKey);
      check('keyboard opens both language options',await waitTrue(page,`Boolean(${option('en')})&&Boolean(${option('zh-CN')})`,'keyboard open'),true);
      check('selected option receives initial keyboard focus',await waitTrue(page,`document.activeElement===${option(locale==='en'?'zh-CN':'en')}`,'selected option focus'),true);
      await key(page,arrow);
      check('arrow focuses opposite option',await waitTrue(page,`document.activeElement===${option(locale)}`,'arrow focus'),true);
      await key(page,'Enter');await pause();
      check(`${openKey}/${arrow}: locale and radio`,await evaluate(page,`[document.documentElement.lang,${radio(locale)}.checked]`),[locale,true]);
      check('same header/content/route before new lookup',await evaluate(page,`window.__header.isConnected&&window.__panel.isConnected&&location.pathname===window.__path`),true);
      check('selection returns focus to trigger',await evaluate(page,`document.activeElement===window.__header`),true);
      check('zero switch HTTP any method',ledger.slice(from),[]);
      check('zero page HTTP any method',http.slice(networkFrom).filter(r=>r.sessionId===page.sessionId),[]);
      check('zero WS/SSE restarts/messages',transport.slice(transportFrom).filter(r=>r.sessionId===page.sessionId),[]);
    }
    await key(page,'Enter');await waitTrue(page,`Boolean(document.querySelector('[role="listbox"]'))`,'open for Escape');await key(page,'Escape');await pause();
    check('Escape closes and returns focus',await evaluate(page,`!document.querySelector('[role="listbox"]')&&document.activeElement===window.__header`),true);
    await key(page,'Tab');
    check('Tab reaches adjacent theme',await evaluate(page,`document.activeElement===${theme}`),true);
    await key(page,'Space');
    check('keyboard dark theme, locale unchanged',await evaluate(page,`[document.documentElement.dataset.theme,document.documentElement.lang,${theme}.getAttribute('aria-label')]`),['dark','en',tr('en','shell.switchToLight')]);
    await clickSrc(page,theme);
    check('pointer restores light theme',await evaluate(page,`document.documentElement.dataset.theme`),'light');
    for(const locale of ['zh-CN','en']){
      await choose(page,locale);
      check('header agrees with Settings and persistence',await evaluate(page,`[${radio(locale)}.checked,localStorage.getItem('${KEY}'),document.querySelector('[role="status"]').textContent]`),[true,locale,tr(locale,'settings.preferences.status.durable')]);
      await clickSrc(page,radio(locale==='en'?'zh-CN':'en'));
      check('Settings agrees with header',await evaluate(page,`${header}.textContent.includes(${JSON.stringify(locale==='en'?'简体中文':'English')})`),true);
    }
    await choose(page,'zh-CN');
    const loaded=cdp.waitFor('Page.loadEventFired',{sessionId:page.sessionId});await cdp.send('Page.reload',{},page.sessionId);await loaded;
    check('real reload retains saved header language',await waitTrue(page,`document.documentElement.lang==='zh-CN'&&${radio('zh-CN')}?.checked&&${header}?.textContent.includes('简体中文')`,'reload saved choice'),true);
    await clickSrc(page,theme);
    check('Chinese pointer theme keeps language and translates label',await evaluate(page,`[document.documentElement.dataset.theme,document.documentElement.lang,${theme}.getAttribute('aria-label')]`),['dark','zh-CN',tr('zh-CN','shell.switchToLight')]);
    await key(page,'Space');
    check('Chinese keyboard returns to light without a language change',await evaluate(page,`[document.documentElement.dataset.theme,document.documentElement.lang]`),['light','zh-CN']);
    const other=await openPage(base+PREFS,{init:observeWrites});
    await ready(other);
    for(const locale of ['en','zh-CN']){
      await choose(other,locale);
      check('real second-tab choice updates first header/Settings',await waitTrue(page,`document.documentElement.lang==='${locale}'&&${radio(locale)}.checked&&${header}.textContent.includes(${JSON.stringify(locale==='en'?'English':'简体中文')})`,'cross-tab agreement'),true);
      check('mirroring tab does not write locale back',await evaluate(page,'window.__headerWrites'),[]);
    }
    check('originating tab writes exactly its two explicit choices',await evaluate(other,'window.__headerWrites'),['en','zh-CN']);
    await closePage(other);await closePage(page);endCase();
  }
  if(['all','draft'].includes(selectedCase)){
    beginCase('H-draft','header and other-tab selection preserve mounted authored editor; original Cancel/navigation');
    const page=await openPage(`${base}/orgs/test-org/agents/lead`,{init:`${seedLocale('en')}\n${chineseNavigator}`});
    const exists=await ready(page);check('shipping header for editor exists',exists,true);
    if(!exists){await closePage(page);endCase();return;}
    if(!await waitTrue(page,`Boolean(${edit})&&!${edit}.disabled`,'valid prompt editor'))throw new Error('valid Agents fixture editor unavailable');
    await clickSrc(page,edit);
    await waitTrue(page,`Boolean(document.querySelector('main textarea'))`,'actual editable prompt');
    const draft='Authored English line\n作者中文行\n  exact spacing';
    await evaluate(page,`window.__editor=document.querySelector('main textarea');window.__editor.focus();window.__editor.select();window.__section=window.__editor.closest('section');true;`);
    await cdp.send('Input.insertText',{text:draft},page.sessionId);
    await evaluate(page,`window.__editor.setSelectionRange(3,8)`);await pause();
    async function retained(locale,focused){
      // FIRST observation uses retained references, before lookup or refocus.
      check('retained editor and section connected',await evaluate(page,`window.__editor.isConnected&&window.__section.isConnected&&window.__section.contains(window.__editor)`),true);
      check(`${locale}: exact draft, selection and expected focus`,await evaluate(page,`[window.__editor.value,window.__editor.selectionStart,window.__editor.selectionEnd,document.activeElement===window.__editor]`),[draft,3,8,focused]);
      check('same actual shipping node',await evaluate(page,`window.__editor===document.querySelector('main textarea')`),true);
    }
    async function silence(action,locale,focused){
      const from=ledger.length,networkFrom=http.length,transportFrom=transport.length;
      await action();await pause();await retained(locale,focused);
      check('zero editor switch HTTP any method',ledger.slice(from),[]);
      check('zero editor page HTTP any method',http.slice(networkFrom).filter(r=>r.sessionId===page.sessionId),[]);
      check('zero editor WS/SSE restarts/messages',transport.slice(transportFrom).filter(r=>r.sessionId===page.sessionId),[]);
    }
    for(const locale of ['zh-CN','en'])await silence(()=>choose(page,locale),locale,false);
    const other=await openPage(base+PREFS);await ready(other);await pause();
    await evaluate(page,`window.__editor.focus();window.__editor.setSelectionRange(3,8)`);
    for(const locale of ['zh-CN','en'])await silence(async()=>{
      await choose(other,locale);
      check('other-tab choice delivered',await waitTrue(page,`document.documentElement.lang==='${locale}'`,'editor storage event'),true);
    },locale,true);
    const from=ledger.length;
    await clickSrc(page,`[...window.__section.querySelectorAll('button')].find(b=>b.textContent.trim()==='Cancel')`);
    check('original Cancel removes authored draft',await waitTrue(page,`!document.querySelector('main textarea')`,'Cancel'),true);
    check('Cancel issues no PUT',ledger.slice(from).filter(r=>r.method==='PUT'),[]);
    await clickSrc(page,`document.querySelector('a[href="/orgs/test-org/settings"]')`);
    check('original Settings navigation',await waitTrue(page,`location.pathname==='/orgs/test-org/settings/assistant'`,'navigation'),true);
    await closePage(other);await closePage(page);endCase();
  }
}
