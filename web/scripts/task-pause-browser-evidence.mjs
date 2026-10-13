#!/usr/bin/env node
/**
 * TASK-10560 P18/G10: ordinary-build UI/transport evidence only.
 * TASK-10919 (founder THR-292 seq41): the standalone Task pause overview panel is removed
 * from the Tasks page; this harness asserts its absence and zero overview reads.
 * TASK-10935 (founder THR-292 seq45): ALL pause UI lives on Task detail only. List rows render
 * no pause controls/labels/evidence/blockers; actions, refusals and generation run on detail.
 * TASK-11158 (founder THR-292 seq77): detail header normal/pausing/paused states, keyboard disclosure,
 * mobile/desktop document order, contrast, focus ring and reduced-motion indicator.
 * TASK-11169: expanded owner/reason, waiting/incomplete/observed notes and links each measured >= 4.5:1 on a
 * held pausing root with task/job plus unknown evidence; the focus ring must be a visible (alpha, spread) layer.
 * Synthetic HTTP transcripts do not prove backend Pause enforcement, persistence,
 * provider containment or safe restart. No live daemon, dependency or shipping seam.
 * Composes the existing mode-A static/API server; drives owned Chrome over CDP.
 */
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, readdirSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createServer } from './screenshot-harness/harness.mjs';
import { en } from '../src/lib/i18n/locales/en.ts';
import { zhCN } from '../src/lib/i18n/locales/zh-CN.ts';

const arg = (key, fallback) => { const i = process.argv.indexOf(`--${key}`); return i < 0 ? fallback : process.argv[i + 1]; };
const dist = resolve(arg('dist', 'dist'));
const out = resolve(arg('out', '/tmp/task-pause-evidence'));
const head = arg('head', 'unknown');
const chromeBin = arg('chrome', '/usr/bin/google-chrome');
const digest = data => createHash('sha256').update(data).digest('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const tr = (locale, key) => (locale === 'en' ? en : zhCN)[key];
const quote = JSON.stringify;
const stamp = '2026-10-11T00:00:00Z';
const ledger = [], checks = [], screenshots = [], hung = [], browserEvents = [], contrast = [];
const blockers = ['task_session','job','launch','result_processing','unknown'].map(kind => ({kind,task_id:'ROOT-PAUSING',job_id:kind==='job'?'JOB-9':null,owner_kind:'raw-owner',reason:'raw_reason',observed_at:stamp}));
let scenario = 'populated', control = 'success', toggled = { held: false, generation: 4 };
function projection(id, status = 'in_progress', extra = {}) {
  return {org_slug:'pause-org',root_task_id:id,lifecycle_status:status,held:false,effective_hold:false,generation:4,control_state:'unheld',paused_at:null,resumed_at:null,blockers:[],evidence_complete:true,observed_at:stamp,...extra};
}
function root(id,status='in_progress',extra={}) {
  return {task_id:id,team:'engineering',brief:`Authored ${id} — preserve 中文`,status,block_kind:null,parent_task_id:null,revisit_of_task_id:null,assigned_agent:'dev_agent',created_at:stamp,updated_at:stamp,closed_at:null,cancelled_at:null,session_timeout_seconds:null,severity_rollup:status,pause:projection(id,status),...extra};
}
function roots() {
  return [root('ROOT-RUN','in_progress',{pause:projection('ROOT-RUN','in_progress',{...toggled,effective_hold:toggled.held,control_state:toggled.held?'paused':'unheld'})}),
    root('ROOT-PARKED','in_progress',{block_kind:'delegated'}), root('ROOT-PENDING','pending'),
    root('ROOT-PAUSING','in_progress',{pause:projection('ROOT-PAUSING','in_progress',{held:true,effective_hold:true,control_state:'pausing',blockers,evidence_complete:false})}),
    root('ROOT-PAUSED','in_progress',{pause:projection('ROOT-PAUSED','in_progress',{held:true,effective_hold:true,control_state:'paused'})}),
    root('ROOT-DRAINING','in_progress',{pause:projection('ROOT-DRAINING','in_progress',{held:true,effective_hold:true,control_state:'pausing',blockers:[{kind:'job',task_id:'ROOT-DRAINING',job_id:'JOB-4127',owner_kind:'raw-owner',reason:'raw_reason',observed_at:stamp},{kind:'task_session',task_id:'CHILD-1',job_id:null,owner_kind:'raw-owner',reason:'raw_reason',observed_at:stamp},{kind:'unknown',task_id:'ROOT-DRAINING',job_id:null,owner_kind:'unknown-owner',reason:'unknown_reason',observed_at:stamp}],evidence_complete:false})}),
    root('ROOT-ESCALATED','escalated',{pause:projection('ROOT-ESCALATED','escalated',{held:true,effective_hold:true,control_state:'paused'})}),
    root('ROOT-FAILED','failed',{pause:projection('ROOT-FAILED','failed',{held:true,blockers:[blockers[4]],evidence_complete:false})}),
    root('ROOT-MISSING','in_progress',{pause:undefined})];
}
async function api(req,res) {
  const url=new URL(req.url,'http://isolated');
  let raw=''; for await(const chunk of req) raw+=chunk;
  const entry={method:req.method,path:url.pathname,search:url.search,body:raw?JSON.parse(raw):null,observed_at:new Date().toISOString()};ledger.push(entry);
  const end=(body,status=200)=>{entry.status=status;res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));};
  if(url.pathname.endsWith('/auth/bootstrap')) return end({token:'ui-transport-only-token'});
  if(url.pathname==='/api/v1/orgs') return end({orgs:[{slug:'pause-org',root:'/synthetic-only'}],broken:[]});
  if(url.pathname.endsWith('/dashboard/summary')) return end({org_age_days:1});
  if(url.pathname.endsWith('/events')) {entry.status=200;res.writeHead(200,{'Content-Type':'text/event-stream'});res.write(': UI fixture\n\n');hung.push(res);return;}
  if(url.pathname.endsWith('/tasks/roots')) {
    // Finite ordinary Tasks list states; the attention (status=escalated) reader stays populated.
    const attention=url.searchParams.get('status')==='escalated';
    if(!attention&&scenario==='loading') await sleep(750);
    if(!attention&&scenario==='error') return end({detail:'raw_read_error'},503);
    if(scenario==='empty') return end({tasks:[],next_cursor:null});
    return end({tasks:attention?roots().filter(t=>t.status==='escalated'):roots(),next_cursor:null});
  }
  if(req.method==='POST' && /\/ROOT-RUN\/(pause|resume)$/.test(url.pathname)) {
    await sleep(180);
    if(control==='conflict') {toggled={held:true,generation:9};return end({detail:{code:'control_generation_conflict'}},409);}
    const held=url.pathname.endsWith('/pause');toggled={held,generation:held?5:6};
    return end({changed:true,pause:projection('ROOT-RUN','in_progress',{...toggled,effective_hold:held,control_state:held?'paused':'unheld'})});
  }
  const match=url.pathname.match(/\/tasks\/([^/]+)(?:\/(recall))?$/);
  if(match){const task=match[1]==='CHILD-1'?root('CHILD-1','in_progress',{parent_task_id:'ROOT-PAUSING',pause:projection('ROOT-PAUSING','in_progress',{held:true,effective_hold:true,control_state:'pausing',blockers,evidence_complete:false})}):roots().find(t=>t.task_id===match[1]);
    if(match[2])return end({task_id:match[1],assigned_agent:'dev_agent',brief:task?.brief??'raw',status:task?.status??'pending',output_summary:null,children:[]});
    return end({task,pause:task?.pause,results:[],audit_log:[],revisit_chain:[],direct_revisits:[],active_chain:null,work_status:{applicable:false,state:'not_applicable',label:'Not applicable',reason:null}});
  }
  if(/\/jobs\/?$/.test(url.pathname)) return end({jobs:[],next_cursor:null});
  return end({});
}
class CDP {
  constructor(url){this.ws=new WebSocket(url);this.id=0;this.pending=new Map();this.ready=new Promise((ok,fail)=>{this.ws.onopen=ok;this.ws.onerror=()=>fail(new Error('CDP connection failed'));});this.ws.onmessage=event=>{const message=JSON.parse(event.data);if(['Runtime.exceptionThrown','Network.loadingFailed'].includes(message.method))browserEvents.push(message);const pending=this.pending.get(message.id);if(pending){this.pending.delete(message.id);message.error?pending.reject(new Error(JSON.stringify(message.error))):pending.resolve(message.result);}};}
  async send(method,params={},sessionId){await this.ready;const id=++this.id;const result=new Promise((resolve,reject)=>{const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error(`CDP timeout: ${method}`));},10000);this.pending.set(id,{resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}});});this.ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));return result;}
}
async function main(){
  if(!existsSync(join(dist,'index.html')))throw new Error('ordinary production dist required');
  mkdirSync(out,{recursive:true});
  const profile=mkdtempSync('/tmp/pause-ui-');
  const blank=join(dist,'pause-locale-switch.html');if(existsSync(blank))throw new Error('refuse overwrite blank page');writeFileSync(blank,'<!doctype html><title>Isolated locale storage writer</title>');
  const server=await createServer({root:dist,api:[{path:/^\/api\//,handler:api}]});
  const chrome=spawn(chromeBin,['--headless=new','--remote-debugging-port=0',`--user-data-dir=${profile}`,'--no-sandbox','--no-first-run','--no-proxy-server','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--disable-crash-reporter','--disable-breakpad','--disable-extensions','about:blank'],{stdio:['ignore','ignore','pipe'],env:{...process.env,TMPDIR:profile,XDG_CONFIG_HOME:join(profile,'config'),XDG_CACHE_HOME:join(profile,'cache')}});
  const chromeErrors=[];chrome.stderr.on('data',chunk=>chromeErrors.push(chunk));
  let cdp;
  const check=(name,actual,expected=true)=>{const pass=JSON.stringify(actual)===JSON.stringify(expected);checks.push({name,actual,expected,pass});if(!pass)console.error('FAIL',name,JSON.stringify(actual),'expected',JSON.stringify(expected));};
  try{
    const until=Date.now()+10000;while(!existsSync(join(profile,'DevToolsActivePort'))&&Date.now()<until)await sleep(40);
    if(!existsSync(join(profile,'DevToolsActivePort')))throw new Error(`Chrome startup failed: code=${chrome.exitCode} signal=${chrome.signalCode}; ${Buffer.concat(chromeErrors).toString()}`);
    const [port,path]=readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n');cdp=new CDP(`ws://127.0.0.1:${port}${path.trim()}`);
    const browser=await cdp.send('Browser.getVersion');
    async function evaluate(page,expression){const result=await cdp.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},page.sessionId);if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);return result.result.value;}
    async function wait(page,expression){const until=Date.now()+5000;while(Date.now()<until){if(await evaluate(page,`!!document.body && (${expression})`))return;await sleep(40);}const state=await evaluate(page,'({url:location.href,title:document.title,readyState:document.readyState,visibility:document.visibilityState,text:document.body?.textContent?.slice(0,1200)})');throw new Error(`UI wait timed out: ${expression}; ${JSON.stringify(state)}`);}
    async function open(route,locale='en',width=1440,height=900,theme='light'){
      const {targetId}=await cdp.send('Target.createTarget',{url:'about:blank'});const {sessionId}=await cdp.send('Target.attachToTarget',{targetId,flatten:true});const page={targetId,sessionId};
      await cdp.send('Page.enable',{},sessionId);await cdp.send('Runtime.enable',{},sessionId);await cdp.send('Network.enable',{},sessionId);await cdp.send('Page.bringToFront',{},sessionId);await wait(page,"location.href==='about:blank' && document.readyState==='complete'");await cdp.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false},sessionId);
      await cdp.send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('happyranch.ui.locale',${quote(locale)});localStorage.setItem('happyranch.theme',${quote(theme)});`},sessionId);
      await cdp.send('Page.navigate',{url:server.url+route},sessionId);return page;
    }
    const close=page=>cdp.send('Target.closeTarget',{targetId:page.targetId});
    async function shot(page,name,metadata){await cdp.send('Page.bringToFront',{},page.sessionId);await sleep(100);const {data}=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},page.sessionId);const bytes=Buffer.from(data,'base64');const file=join(out,`${name}.png`);writeFileSync(file,bytes);screenshots.push({file,sha256:digest(bytes),...metadata});}
    const rootButton=(id,label)=>`[...document.querySelectorAll('main [data-task-pause="${id}"] button')].find(b=>b.textContent.trim()===${quote(label)})`;
    const button=label=>`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${quote(label)})`;
    // Panel absence in BOTH locales (removed presentation must not reappear under either catalog).
    const panelAbsent=`![...document.querySelectorAll('section[aria-label],h2')].some(e=>['Task pause overview','任务暂停概览'].some(t=>(e.getAttribute('aria-label')||e.textContent).trim()===t))`;
    const overviewReads=()=>ledger.filter(x=>x.path.endsWith('/pause-overview')).length;
    // seq45: the rendered list carries NO pause UI in either catalog; ordinary lifecycle badges remain.
    const PAUSE_COPY=['tasks.pause.evidenceUnknown','tasks.pause.pausing','tasks.pause.paused','tasks.pause.unavailable','tasks.pause.inherited','tasks.pause.waitingNote','tasks.pause.evidenceIncomplete','tasks.pause.observed','tasks.pause.terminalDrain','tasks.pause.blocker.task','tasks.pause.blocker.unknown'].flatMap(k=>['en','zh-CN'].map(l=>String(tr(l,k)).split('{')[0].trim())).filter(Boolean);
    const listPauseFree=`(()=>{const t=document.body.textContent;return document.querySelector('[data-task-pause]')===null&&![...document.querySelectorAll('button')].some(b=>['Pause','Resume','暂停','恢复'].includes(b.textContent.trim()))&&!${quote(PAUSE_COPY)}.some(c=>t.includes(c))&&!t.includes('raw_reason')&&document.querySelector('main details')===null;})()`;
    async function enter(page,source){await cdp.send('Page.bringToFront',{},page.sessionId);await evaluate(page,`(()=>{const button=${source};if(!button)throw new Error('button missing');button.scrollIntoView({block:'center'});button.focus();return true;})()`);await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'},page.sessionId);await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13},page.sessionId);}
    async function switchLocale(page,locale){
      const {targetId}=await cdp.send('Target.createTarget',{url:server.url+'/pause-locale-switch.html'});const {sessionId}=await cdp.send('Target.attachToTarget',{targetId,flatten:true});await cdp.send('Runtime.enable',{},sessionId);await wait({sessionId},`location.href===${quote(server.url+'/pause-locale-switch.html')} && document.readyState==='complete'`);
      await evaluate({sessionId},`localStorage.setItem('happyranch.ui.locale',${quote(locale)})`);await cdp.send('Target.closeTarget',{targetId});await cdp.send('Page.bringToFront',{},page.sessionId);await wait(page,`document.documentElement.lang===${quote(locale)}`);await sleep(100);
    }
    for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]])for(const theme of ['light','dark']){
      scenario='populated';toggled={held:false,generation:4};const name=`${locale}-${width}-${theme}`;
      const page=await open('/orgs/pause-org/tasks',locale,width,height,theme);await wait(page,"!!document.querySelector('[data-tasks-responsive-list] a[href=\"/orgs/pause-org/tasks/ROOT-RUN\"]') && !!document.querySelector('[data-tasks-responsive-list] a[href=\"/orgs/pause-org/tasks/ROOT-FAILED\"]')");await sleep(200);
      check(`${name} no pause overview panel`,await evaluate(page,panelAbsent));
      check(`${name} html locale`,await evaluate(page,'document.documentElement.lang'),locale);
      check(`${name} theme`,await evaluate(page,'document.documentElement.dataset.theme'),theme);
      check(`${name} page fits`,await evaluate(page,'document.documentElement.scrollWidth <= innerWidth + 1'));
      check(`${name} list has no pause UI`,await evaluate(page,listPauseFree));
      check(`${name} ordinary lifecycle badges`,await evaluate(page,"['in_progress','pending','escalated','failed'].every(s=>[...document.querySelectorAll('main *')].some(e=>e.textContent.trim()===s))"));
      check(`${name} row navigation`,await evaluate(page,"['ROOT-RUN','ROOT-PARKED','ROOT-PENDING','ROOT-PAUSING','ROOT-PAUSED','ROOT-FAILED','ROOT-MISSING'].every(id=>!!document.querySelector(`[data-tasks-responsive-list] a[href=\"/orgs/pause-org/tasks/${id}\"]`))"));
      await shot(page,`${name}-list`,{locale,theme,width,height,state:'populated list: lifecycle only, pause UI absent'});
      await close(page);
      const detail=await open('/orgs/pause-org/tasks/ROOT-PAUSING',locale,width,height,theme);await wait(detail,"!!document.querySelector('[data-task-pause] button')");
      check(`${name} detail Resume during drain`,await evaluate(detail,`document.querySelector('[data-task-pause] button').textContent.trim()`),tr(locale,'tasks.pause.resume'));
      check(`${name} detail pausing state label`,await evaluate(detail,`document.querySelector('[data-task-pause]').textContent.includes(${quote(tr(locale,'tasks.pause.pausing'))})`));
      await evaluate(detail,"document.querySelectorAll('[data-task-pause] details').forEach(d=>d.open=true)");
      check(`${name} detail typed blockers shown`,await evaluate(detail,`document.querySelector('[data-task-pause]').textContent.includes(${quote(tr(locale,'tasks.pause.blocker.unknown'))})&&document.querySelector('[data-task-pause]').textContent.includes('raw_reason')`));
      check(`${name} detail control widths`,await evaluate(detail,"[...document.querySelectorAll('[data-task-pause] button,[data-task-pause] summary')].every(b=>{const r=b.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1;})"));
      check(`${name} detail fits`,await evaluate(detail,'document.documentElement.scrollWidth <= innerWidth + 1'));
      await shot(detail,`${name}-detail`,{locale,theme,width,height,state:'pausing detail'});
      // Original Cancel dialog draft/focus/selection/node preservation both directions.
      await enter(detail,button(tr(locale,'tasks.detail.cancel')));await wait(detail,"!!document.querySelector('[role=\"dialog\"] textarea')");
      await evaluate(detail,"(()=>{const t=document.querySelector('[role=\"dialog\"] textarea');t.focus();window.pauseField=t;window.pauseDialog=t.closest('[role=\"dialog\"]');return true;})()");
      await cdp.send('Input.insertText',{text:'Preserved draft 中文'},detail.sessionId);await evaluate(detail,'pauseField.setSelectionRange(2,8)');
      for(const next of [locale==='en'?'zh-CN':'en',locale]){
        const from=ledger.length;await switchLocale(detail,next);
        check(`${name}->${next} draft/focus/selection`,await evaluate(detail,"[pauseField===document.querySelector('[role=\"dialog\"] textarea'),pauseDialog.isConnected,pauseField.value,document.activeElement===pauseField,pauseField.selectionStart,pauseField.selectionEnd]"),[true,true,'Preserved draft 中文',true,2,8]);
        check(`${name}->${next} zero locale HTTP`,ledger.slice(from),[]);
      }
      await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},detail.sessionId);await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},detail.sessionId);await wait(detail,"!document.querySelector('[role=\"dialog\"] textarea')");
      await wait(detail,`document.activeElement.textContent.trim()===${quote(tr(locale,'tasks.detail.cancel'))}`);
      check(`${name} Cancel focus return`,await evaluate(detail,`document.activeElement.textContent.trim()`),tr(locale,'tasks.detail.cancel'));
      await close(detail);
    }
    // THR-292 seq77 task-detail header: normal / pausing (closed+open via keyboard) / paused, document-flow order,
    // contrast, focus ring and reduced-motion indicator. Same synthetic transport; UI evidence only.
    const region=id=>`document.querySelector('main [data-task-pause="${id}"]')`;
    const rect=sel=>`(()=>{const e=${sel};if(!e)return null;const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};})()`;
    // Canvas-resolved sRGB (computed colours are oklch) -> WCAG contrast of an element's text over its own/inherited background.
    const contrastOf=sel=>`((e)=>{const c=document.createElement('canvas').getContext('2d');const rgb=v=>{c.clearRect(0,0,1,1);c.fillStyle='#000';c.fillStyle=v;c.fillRect(0,0,1,1);return [...c.getImageData(0,0,1,1).data];};let b=e,bg=[0,0,0,0];while(b&&(bg=rgb(getComputedStyle(b).backgroundColor))[3]===0)b=b.parentElement;const fg=rgb(getComputedStyle(e).color);const L=([r,g,b])=>{const f=x=>{x/=255;return x<=0.03928?x/12.92:((x+0.055)/1.055)**2.4;};return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b);};const [x,y]=[L(fg),L(bg)].sort((m,n)=>n-m);return Math.round(((x+0.05)/(y+0.05))*100)/100;})(${sel})`;
    const ringVisible=sel=>`((e)=>{const c=document.createElement('canvas').getContext('2d');const alpha=v=>{c.clearRect(0,0,1,1);c.globalCompositeOperation='copy';c.fillStyle='rgba(0,0,0,0)';c.fillStyle=v;c.fillRect(0,0,1,1);return c.getImageData(0,0,1,1).data[3];};return [...getComputedStyle(e).boxShadow.matchAll(/((?:rgba?|oklch|oklab|lab|color)\\([^)]*\\)|#[0-9a-f]+)\\s+(-?[\\d.]+)px\\s+(-?[\\d.]+)px\\s+(-?[\\d.]+)px\\s+(-?[\\d.]+)px/gi)].some(m=>alpha(m[1])>0&&parseFloat(m[5])>=1);})(${sel})`;
    const pressKey=async(page,key,code,vk)=>{await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:vk,...(key==='Enter'?{text:'\r'}:{})},page.sessionId);await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:vk},page.sessionId);};
    for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]])for(const theme of ['light','dark']){
      scenario='populated';toggled={held:false,generation:4};const name=`${locale}-${width}-${theme}`;const mobile=width<768;
      const normal=await open('/orgs/pause-org/tasks/ROOT-RUN',locale,width,height,theme);await wait(normal,`!!${rootButton('ROOT-RUN',tr(locale,'tasks.pause.pause'))}`);await sleep(150);
      check(`${name} seq77 normal: region is only the compact Pause`,await evaluate(normal,`${region('ROOT-RUN')}.textContent.trim()`),tr(locale,'tasks.pause.pause'));
      check(`${name} seq77 normal: Pause shares the row with Revisit/Cancel/audit`,await evaluate(normal,`(()=>{const row=${region('ROOT-RUN')}.parentElement;return [${quote(tr(locale,'tasks.detail.revisit'))},${quote(tr(locale,'tasks.detail.cancel'))}].every(n=>[...row.querySelectorAll('button')].some(b=>b.textContent.trim()===n))&&!!row.querySelector('a[href="/orgs/pause-org/audit?task_id=ROOT-RUN"]')&&!row.querySelector('details');})()`));
      check(`${name} seq77 normal: one visual row`,await evaluate(normal,`(()=>{const p=${rootButton('ROOT-RUN',tr(locale,'tasks.pause.pause'))}.getBoundingClientRect(),r=${button(tr(locale,'tasks.detail.revisit'))}.getBoundingClientRect();return Math.abs((p.top+p.bottom)/2-(r.top+r.bottom)/2)<3;})()`));
      check(`${name} seq77 normal fits`,await evaluate(normal,'document.documentElement.scrollWidth <= innerWidth + 1'));
      await shot(normal,`${name}-seq77-normal`,{locale,theme,width,height,state:'seq77 normal root header'});await close(normal);
      const drain=await open('/orgs/pause-org/tasks/ROOT-DRAINING',locale,width,height,theme);await wait(drain,`!!${region('ROOT-DRAINING')}?.querySelector('details')`);await sleep(150);
      check(`${name} seq77 pausing: disclosure closed by default`,await evaluate(drain,`${region('ROOT-DRAINING')}.querySelector('details').open`),false);
      check(`${name} seq77 pausing: waiting indicator copy`,await evaluate(drain,`${region('ROOT-DRAINING')}.textContent.includes(${quote(tr(locale,'tasks.pause.pausing'))})`));
      check(`${name} seq77 pausing: secondary Resume`,await evaluate(drain,`/\\bbg-primary\\b/.test((${rootButton('ROOT-DRAINING',tr(locale,'tasks.pause.resume'))}).className)`),false);
      const g=await evaluate(drain,`({ind:${rect(`${region('ROOT-DRAINING')}.querySelector('span.rounded-pill')`)},sum:${rect(`${region('ROOT-DRAINING')}.querySelector('summary')`)},rev:${rect(button(tr(locale,'tasks.detail.revisit')))},audit:${rect(`document.querySelector('main a[href="/orgs/pause-org/audit?task_id=ROOT-DRAINING"]')`)}})`);
      if(mobile)check(`${name} seq77 pausing mobile order: indicator, disclosure, ordinary actions`,g.ind.bottom<=g.sum.top+1&&g.sum.bottom<=g.rev.top+1&&g.sum.bottom<=g.audit.top+1);
      else check(`${name} seq77 pausing desktop: indicator and actions on one row, disclosure below`,Math.abs((g.ind.top+g.ind.bottom)/2-(g.rev.top+g.rev.bottom)/2)<3&&g.sum.top>=Math.max(g.ind.bottom,g.rev.bottom)-1);
      check(`${name} seq77 pausing: indicator contrast >= 4.5`,await evaluate(drain,`${contrastOf(`${region('ROOT-DRAINING')}.querySelector('span.rounded-pill')`)}>=4.5`));
      check(`${name} seq77 pausing: summary contrast >= 4.5`,await evaluate(drain,`${contrastOf(`${region('ROOT-DRAINING')}.querySelector('summary')`)}>=4.5`));
      check(`${name} seq77 pausing: indicator pulses`,await evaluate(drain,`getComputedStyle(${region('ROOT-DRAINING')}.querySelector('span.rounded-pill > span')).animationName!=='none'`));
      await cdp.send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]},drain.sessionId);await sleep(50);
      check(`${name} seq77 pausing: reduced motion stops the indicator animation`,await evaluate(drain,`getComputedStyle(${region('ROOT-DRAINING')}.querySelector('span.rounded-pill > span')).animationName`),'none');
      await shot(drain,`${name}-seq77-pausing-closed`,{locale,theme,width,height,state:'seq77 pausing, disclosure closed'});
      // Keyboard: Tab from Resume reaches the summary with a visible focus ring; Enter toggles it.
      await evaluate(drain,`(()=>{(${rootButton('ROOT-DRAINING',tr(locale,'tasks.pause.resume'))}).focus();return true;})()`);await pressKey(drain,'Tab','Tab',9);await sleep(80);
      check(`${name} seq77 pausing: Tab focuses summary with focus-visible ring`,await evaluate(drain,`(()=>{const s=document.activeElement;return s.tagName==='SUMMARY'&&s.matches(':focus-visible')&&${ringVisible('s')};})()`));
      await pressKey(drain,'Enter','Enter',13);await sleep(120);
      check(`${name} seq77 pausing: Enter opens disclosure`,await evaluate(drain,`${region('ROOT-DRAINING')}.querySelector('details').open`),true);
      const small=await evaluate(drain,`[...${region('ROOT-DRAINING')}.querySelectorAll('details li > span:last-child, details > div > p, details a')].map(el=>({text:el.textContent.trim(),font:getComputedStyle(el).fontSize,ratio:${contrastOf('el')}}))`);
      contrast.push({name,targets:small});const notes=[tr(locale,'tasks.pause.waitingNote'),tr(locale,'tasks.pause.evidenceIncomplete')];const observedPrefix=String(tr(locale,'tasks.pause.observed')).split('{')[0];
      check(`${name} seq77 pausing open: owner/reason rows, waiting note, incomplete warning, observed time and links all rendered`,[small.filter(x=>/raw_reason|unknown_reason/.test(x.text)&&x.text.includes(' · ')&&!x.text.startsWith('JOB-')).length,notes.every(n=>small.some(x=>x.text===n)),small.some(x=>x.text.startsWith(observedPrefix)&&x.text.length>observedPrefix.length),['JOB-4127','CHILD-1','ROOT-DRAINING'].every(id=>small.some(x=>x.text===id))],[3,true,true,true]);
      check(`${name} seq77 pausing open: every 12px metadata/note/link target contrast >= 4.5 on its inherited background`,small.filter(x=>!(x.ratio>=4.5)).map(x=>`${x.text} ${x.font} ${x.ratio}`),[]);
      check(`${name} seq77 pausing: genuine job/task links, no fabricated title/start/agent`,await evaluate(drain,`(()=>{const d=${region('ROOT-DRAINING')}.querySelector('details');return !!d.querySelector('a[href="/orgs/pause-org/jobs/JOB-4127"]')&&!!d.querySelector('a[href="/orgs/pause-org/tasks/CHILD-1"]')&&!/started|dev_agent/.test(d.textContent);})()`));
      check(`${name} seq77 pausing open: list rows within viewport`,await evaluate(drain,`[...${region('ROOT-DRAINING')}.querySelectorAll('li,li *')].every(e=>e.getBoundingClientRect().right<=innerWidth+1)&&document.documentElement.scrollWidth<=innerWidth+1`));
      if(mobile){const o=await evaluate(drain,`({list:${rect(`${region('ROOT-DRAINING')}.querySelector('ul')`)},rev:${rect(button(tr(locale,'tasks.detail.revisit')))}})`);check(`${name} seq77 pausing mobile open: list pushes ordinary actions down (document flow)`,o.list.bottom<=o.rev.top+1);}
      await shot(drain,`${name}-seq77-pausing-open`,{locale,theme,width,height,state:'seq77 pausing, disclosure open via keyboard'});
      await pressKey(drain,'Enter','Enter',13);await sleep(120);
      check(`${name} seq77 pausing: Enter closes disclosure`,await evaluate(drain,`${region('ROOT-DRAINING')}.querySelector('details').open`),false);
      await close(drain);
      const paused=await open('/orgs/pause-org/tasks/ROOT-PAUSED',locale,width,height,theme);await wait(paused,`!!${rootButton('ROOT-PAUSED',tr(locale,'tasks.pause.resume'))}`);await sleep(150);
      check(`${name} seq77 paused: primary Resume`,await evaluate(paused,`/\\bbg-primary\\b/.test((${rootButton('ROOT-PAUSED',tr(locale,'tasks.pause.resume'))}).className)`));
      check(`${name} seq77 paused: distinct Paused indicator, no disclosure`,await evaluate(paused,`(()=>{const r=${region('ROOT-PAUSED')};return r.querySelector('span.rounded-pill').textContent.trim()===${quote(tr(locale,'tasks.pause.paused'))}&&!r.querySelector('details');})()`));
      check(`${name} seq77 paused: indicator contrast >= 4.5`,await evaluate(paused,`${contrastOf(`${region('ROOT-PAUSED')}.querySelector('span.rounded-pill')`)}>=4.5`));
      check(`${name} seq77 paused fits`,await evaluate(paused,'document.documentElement.scrollWidth <= innerWidth + 1'));
      await shot(paused,`${name}-seq77-paused`,{locale,theme,width,height,state:'seq77 paused root header'});await close(paused);
    }
    // Actual UI actions on Task detail (seq45), intercepted shipping client requests and refresh receipts.
    scenario='populated';toggled={held:false,generation:4};const page=await open('/orgs/pause-org/tasks/ROOT-RUN');await wait(page,`!!${rootButton('ROOT-RUN','Pause')}`);
    let from=ledger.length;await enter(page,rootButton('ROOT-RUN','Pause'));
    await wait(page,`(${rootButton('ROOT-RUN','Pause')})?.disabled===true`);
    check('Pause disables repeats',await evaluate(page,`(${rootButton('ROOT-RUN','Pause')})?.disabled`));
    await wait(page,`!!${rootButton('ROOT-RUN','Resume')} && !(${rootButton('ROOT-RUN','Resume')}).disabled`);
    check('Pause one shipping POST',ledger.slice(from).filter(x=>x.method==='POST').map(x=>[x.path,x.body]),[['/api/v1/orgs/pause-org/tasks/ROOT-RUN/pause',{expected_generation:4}]]);
    check('Pause refreshed detail without overview read',[ledger.slice(from).some(x=>x.method==='GET'&&x.path.endsWith('/tasks/ROOT-RUN')),ledger.slice(from).some(x=>x.path.endsWith('/pause-overview'))],[true,false]);
    from=ledger.length;await enter(page,rootButton('ROOT-RUN','Resume'));await wait(page,`!!${rootButton('ROOT-RUN','Pause')} && !(${rootButton('ROOT-RUN','Pause')}).disabled`);
    check('Resume one shipping POST',ledger.slice(from).filter(x=>x.method==='POST').map(x=>x.body),[{expected_generation:5}]);
    control='conflict';from=ledger.length;await enter(page,rootButton('ROOT-RUN','Pause'));await wait(page,"!!document.querySelector('[data-task-pause=\"ROOT-RUN\"] [role=\"alert\"]')");await sleep(100);
    check('Conflict no opposing/retry POST',ledger.slice(from).filter(x=>x.method==='POST').map(x=>[x.path,x.body,x.status]),[['/api/v1/orgs/pause-org/tasks/ROOT-RUN/pause',{expected_generation:6},409]]);
    check('Conflict accessible translated copy',await evaluate(page,`document.querySelector('[data-task-pause="ROOT-RUN"] [role="alert"]').textContent.includes(${quote(tr('en','tasks.pause.error.conflict'))})`));
    // Locale switch on mounted detail: same focused Pause node, translated name, zero requests.
    control='success';await evaluate(page,`(()=>{const b=${rootButton('ROOT-RUN','Resume')}||${rootButton('ROOT-RUN','Pause')};window.pauseNode=b;b.focus();return true;})()`);
    for(const next of ['zh-CN','en']){from=ledger.length;await switchLocale(page,next);
      check(`detail ${next} focused pause node kept`,await evaluate(page,`[document.activeElement===window.pauseNode,window.pauseNode.isConnected,[${quote(tr(next,'tasks.pause.pause'))},${quote(tr(next,'tasks.pause.resume'))}].includes(window.pauseNode.textContent.trim())]`),[true,true,true]);
      check(`detail ${next} zero locale HTTP`,ledger.slice(from),[]);}
    // Back to the list after the control refresh: still pause-free.
    from=ledger.length;await evaluate(page,`(()=>{const a=[...document.querySelectorAll('a')].find(a=>a.textContent.trim()===${quote(tr('en','tasks.detail.back'))});a.click();return true;})()`);
    await wait(page,"!!document.querySelector('[data-tasks-responsive-list] a[href=\"/orgs/pause-org/tasks/ROOT-RUN\"]')");await sleep(150);
    check('list after detail action is pause-free',await evaluate(page,listPauseFree));
    await close(page);
    // Ordinary Tasks list finite states (loading/empty/error -> Retry) with the overview panel absent.
    for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]])for(const theme of ['light','dark'])for(const mode of ['loading','empty','error']){
      const name=`${locale}-${width}-${theme}-${mode}`;
      scenario=mode;const page=await open('/orgs/pause-org/tasks',locale,width,height,theme);
      const key=mode==='loading'?'tasks.list.loading':mode==='empty'?'tasks.list.emptyTitle':'tasks.list.errorTitle';
      await wait(page,`document.body.textContent.includes(${quote(tr(locale,key))})`);
      check(`${name} localized list state`,true);check(`${name} no pause overview panel`,await evaluate(page,panelAbsent));check(`${name} list has no pause UI`,await evaluate(page,listPauseFree));check(`${name} page fits`,await evaluate(page,'document.documentElement.scrollWidth <= innerWidth + 1'));
      await shot(page,`${name}`,{locale,width,height,theme,state:`tasks list ${mode}`});
      if(mode==='error'){scenario='populated';await enter(page,button(tr(locale,'common.retry')));await wait(page,"!!document.querySelector('[data-tasks-responsive-list] a[href=\"/orgs/pause-org/tasks/ROOT-RUN\"]')");check(`${name} Retry recovers list`,true);check(`${name} recovered list pause-free`,await evaluate(page,listPauseFree));}
      await close(page);
    }
    for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]])for(const theme of ['light','dark']){
      const name=`${locale}-${width}-${theme}`;
      scenario='populated';const child=await open('/orgs/pause-org/tasks/CHILD-1',locale,width,height,theme);await wait(child,"!!document.querySelector('[data-task-pause=\"CHILD-1\"]')");check(`${name} child has inherited root link`,await evaluate(child,"document.querySelector('[data-task-pause] a').getAttribute('href')"),'/orgs/pause-org/tasks/ROOT-PAUSING');check(`${name} child inherited explanation`,await evaluate(child,`document.querySelector('[data-task-pause]').textContent.includes(${quote(String(tr(locale,'tasks.pause.inherited')).split('{')[0].trim())})`));check(`${name} child has no controls`,await evaluate(child,"document.querySelector('[data-task-pause] button')===null"));check(`${name} child fits`,await evaluate(child,'document.documentElement.scrollWidth <= innerWidth + 1'));await shot(child,`${name}-child`,{locale,width,height,theme,state:'child inherited'});await close(child);
    }
    check('zero GET /tasks/pause-overview across the whole run',overviewReads(),0);
    const assets=readdirSync(join(dist,'assets')).filter(file=>/\.(js|css)$/.test(file)).map(file=>({file,sha256:digest(readFileSync(join(dist,'assets',file)))}));
    const receipt={boundary:'UI/transport only; synthetic HTTP, not backend enforcement/E2E',head,generated_at:new Date().toISOString(),node:{path:process.execPath,version:process.version},chrome:{path:chromeBin,...browser},dist:{path:dist,index_sha256:digest(readFileSync(join(dist,'index.html'))),assets},checks,contrast,screenshots,ledger,browserEvents,failed:checks.filter(c=>!c.pass).length};writeFileSync(join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
    console.log(`${checks.length} browser assertions; ${screenshots.length} screenshots; ${receipt.failed} failures; UI/transport ONLY`);process.exitCode=receipt.failed?1:0;
  }catch(error){writeFileSync(join(out,'failure.json'),JSON.stringify({head,boundary:'UI/transport only',error:error.stack,checks,screenshots,ledger,browserEvents},null,2)+'\n');throw error;}
  finally{for(const response of hung)response.destroy();cdp?.ws.close();if(chrome.exitCode===null&&chrome.signalCode===null)chrome.kill('SIGTERM');await new Promise(resolve=>{if(chrome.exitCode!==null||chrome.signalCode!==null)resolve();else{chrome.once('exit',resolve);setTimeout(()=>{chrome.kill('SIGKILL');resolve();},1000).unref();}});writeFileSync(join(out,'chrome-stderr.log'),Buffer.concat(chromeErrors));server.server.closeAllConnections?.();await server.close();try{rmSync(profile,{recursive:true,force:true,maxRetries:3,retryDelay:100});}catch(error){writeFileSync(join(out,'cleanup-failure.json'),JSON.stringify({profile,error:String(error)})+'\n');process.exitCode=1;}rmSync(blank);}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
