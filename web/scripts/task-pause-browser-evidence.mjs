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
const correctionOnly = process.argv.includes('--correction-only');
const slice = arg('slice','agents');
const view = arg('view','pane');
const selectedLocales = arg('locale', null) ? [arg('locale')] : ['en','zh-CN'];
const drawerDist = arg('drawer-dist', null) ? resolve(arg('drawer-dist')) : null;
const chromeBin = arg('chrome', '/usr/bin/google-chrome');
const digest = data => createHash('sha256').update(data).digest('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const tr = (locale, key) => (locale === 'en' ? en : zhCN)[key];
const quote = JSON.stringify;
const stamp = '2026-10-11T00:00:00Z';
const ledger = [], checks = [], screenshots = [], hung = [], browserEvents = [], contrast = [];
const agentReads = [];
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
const childWait = state => ({state,observed_at:stamp});
const agentStates = ['continuation_pending','pending_children','waiting_children','child_results_received',null,'unavailable','bad-enum','bad-stamp','bad-shape','job','worse','terminal-worse'];
function agentTasks() {return agentStates.map((state,i)=>root(`UI-CARD-${i}`,'in_progress',{
  brief:`Synthetic card ${state ?? 'legacy'}`,block_kind:'delegated',
  ...(state ? {child_wait:childWait(state)} : {}),
  ...(state==='bad-stamp'?{child_wait:{state:'waiting_children',observed_at:'invalid'}}:{}),
  ...(state==='bad-shape'?{child_wait:{...childWait('waiting_children'),extra:true}}:{}),
  ...(state==='job'?{block_kind:'blocked_on_job',child_wait:null}:{}),
  ...(state==='worse'?{severity_rollup:'failed',child_wait:childWait('continuation_pending')}:{}),
  ...(state==='terminal-worse'?{status:'completed',severity_rollup:'in_progress',child_wait:childWait('continuation_pending')}:{}),
}));}
function correctionFixture(id) {
  if(!id.startsWith('UI-')) return null;
  const cases={
    'UI-A01':['continuation_pending',Array(21).fill('completed')],
    'UI-A02':['continuation_pending',Array(21).fill('completed')],
    'UI-A04':['pending_children',['pending','completed']],
    'UI-A07-1':['waiting_children',['in_progress','completed']],
    'UI-A05-1':['child_results_received',['in_progress','completed']],
    'UI-A07-2':['waiting_children',['pending','escalated']],
    'UI-A07-3':['continuation_pending',['completed','failed','cancelled','superseded']],
    'UI-A08-empty':['continuation_pending',[]],
    'UI-A08-legacy':[null,[]],
    'UI-A08-malformed':['bad-enum',[]],
  };
  const historical=id.startsWith('UI-A09-')||id.startsWith('UI-A08-joined-bad-');
  const invalid=id.startsWith('UI-A08-bad-')||id.startsWith('UI-A08-joined-bad-');
  const [waitState,statuses] = cases[id] ?? ['continuation_pending',['completed','failed','cancelled','superseded']];
  const parentStatus=id.startsWith('UI-A09-')?id.slice('UI-A09-'.length):'in_progress';
  const kids=statuses.map((status,i)=>({task_id:`${id}-C${i}`,brief:`Synthetic ${status}`,status,children:[]}));
  let selected=kids.map(c=>c.task_id), width=selected.length;
  let recall={task_id:id,brief:`Synthetic ${id}`,status:parentStatus,children:kids};
  if(invalid){const variant=id.replace(/^UI-A08-(joined-)?bad-/,'');
    if(variant==='missing-id')selected=selected.concat('UI-MISSING');
    if(variant==='duplicate-id')selected=[selected[0],selected[0]];
    if(variant==='malformed-id')selected=[selected[0],42];
    if(variant==='missing-node')recall.children=kids.slice(1);
    if(variant==='duplicate-node')recall.children=kids.concat(kids[0]);
    if(variant==='wrong-root')recall.task_id='UI-WRONG';
    if(variant==='null-node')recall.children=[null];
    if(variant==='bad-status')recall.children=[{...kids[0],status:'unsupported'},...kids.slice(1)];
    if(variant==='missing-recall')recall=null;
    if(variant==='width')width=1.5;
    if(variant==='zero-width')width=0;
    if(variant==='mismatch-width')width=3;
    if(variant==='loading'||variant==='error')recall=null;
    if(variant==='missing-width')width=null;
    if(variant==='empty-ids')selected=[];
  }
  if(id==='UI-A07-1')recall.children.push({task_id:'UI-UNSELECTED',brief:'Unrelated direct child',status:'completed',children:[]});
  const unknown=Array.from({length:16},(_,i)=>({kind:'unknown',task_id:id,job_id:i===0?'UI-JOB-LONG-DESCENDANT-CLEANUP-REFERENCE-000000000000000000000000':`UI-JOB-${i}`,owner_kind:'job',reason:'descendant_cleanup_unconfirmed',observed_at:stamp}));
  const pause=projection(id,parentStatus,{held:true,effective_hold:true,control_state:id==='UI-A01'||id==='UI-A05-1'?'pausing':'paused',blockers:id==='UI-A01'?unknown:id==='UI-A05-1'?[{kind:'result_processing',task_id:id,job_id:null,owner_kind:'task',reason:'result_not_consumed',observed_at:stamp}]:[],evidence_complete:id!=='UI-A01'&&id!=='UI-A05-1'});
  const task=root(id,parentStatus,{block_kind:'delegated',pause,child_wait:waitState?childWait(waitState):undefined,
    ...(historical?{child_wait:parentStatus==='in_progress'?childWait('continuation_pending'):null}:{}),
    ...(!historical&&(invalid||id.includes('A07')||id.includes('A05'))?{active_fanout:JSON.stringify({status:'spawned',width,children_ids:selected})}:{})});
  return {task,recall,audit:historical?[{action:'fanout_join',payload:{width,children_ids:selected}}]:[]};
}
async function api(req,res) {
  const url=new URL(req.url,'http://isolated');
  let raw=''; for await(const chunk of req) raw+=chunk;
  const entry={method:req.method,path:url.pathname,search:url.search,body:raw?JSON.parse(raw):null,observed_at:new Date().toISOString()};ledger.push(entry);
  const end=(body,status=200)=>{entry.status=status;if(entry.agentTasks)entry.payload=body;res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));};
  if(url.pathname.endsWith('/auth/bootstrap')) return end({token:'ui-transport-only-token'});
  if(url.pathname==='/api/v1/orgs') return end({orgs:[{slug:'pause-org',root:'/synthetic-only'}],broken:[]});
  if(correctionOnly && /\/orgs\/[^/]+\/tasks$/.test(url.pathname)) {
    const match=req.method==='GET'&&url.pathname==='/api/v1/orgs/pause-org/tasks'
      && url.searchParams.get('assigned_agent')==='dev_agent'&&url.searchParams.get('limit')==='20';
    if(!match)return end({detail:'wrong selected org/agent/limit'},400);
    entry.agentTasks=true;agentReads.push(entry);return end({tasks:agentTasks(),next_cursor:null});
  }
  if(correctionOnly&&url.pathname==='/api/v1/orgs/pause-org/agents')return end({agents:[{name:'dev_agent',role:'worker',team:'engineering',executor:'claude',model:null,repos:{},description:'Synthetic selected agent',system_prompt:'# Synthetic prompt',revision:'a'.repeat(64)}]});
  if(url.pathname.endsWith('/identities'))return end({identities:[]});
  if(url.pathname.endsWith('/health/prereqs'))return end({prereqs:[]});
  if(url.pathname.endsWith('/executors/runtime/profiles'))return end({profiles:[]});
  if(url.pathname.endsWith('/teams'))return end({teams:[]});
  if(url.pathname.endsWith('/cleanup-activity'))return end({activities:[]});
  if(url.pathname.endsWith('/memory/entries/'))return end({entries:[]});
  if(url.pathname.endsWith('/dashboard/summary')) return end({org_age_days:1});
  if(url.pathname.endsWith('/events')) {entry.status=200;res.writeHead(200,{'Content-Type':'text/event-stream'});res.write(': UI fixture\n\n');hung.push(res);return;}
  if(url.pathname.endsWith('/tasks/roots')) {
    // Finite ordinary Tasks list states; the attention (status=escalated) reader stays populated.
    const attention=url.searchParams.get('status')==='escalated';
    if(!attention&&scenario==='loading') await sleep(750);
    if(!attention&&scenario==='error') return end({detail:'raw_read_error'},503);
    if(scenario==='empty') return end({tasks:[],next_cursor:null});
    return end({tasks:correctionOnly?[...roots(),...['UI-A01','UI-A02','UI-A04','UI-A08-empty','UI-A08-legacy'].map(id=>correctionFixture(id).task)]:attention?roots().filter(t=>t.status==='escalated'):roots(),next_cursor:null});
  }
  if(req.method==='POST' && /\/ROOT-RUN\/(pause|resume)$/.test(url.pathname)) {
    await sleep(180);
    if(control==='conflict') {toggled={held:true,generation:9};return end({detail:{code:'control_generation_conflict'}},409);}
    const held=url.pathname.endsWith('/pause');toggled={held,generation:held?5:6};
    return end({changed:true,pause:projection('ROOT-RUN','in_progress',{...toggled,effective_hold:held,control_state:held?'paused':'unheld'})});
  }
  const match=url.pathname.match(/\/tasks\/([^/]+)(?:\/(recall))?$/);
  if(match && correctionOnly && correctionFixture(match[1])){const fixture=correctionFixture(match[1]);if(match[2]){if(match[1].endsWith('loading'))await sleep(600);if(match[1].endsWith('error'))return end({detail:'synthetic recall unavailable'},503);return end(fixture.recall);}return end({task:fixture.task,pause:fixture.task.pause,results:[],audit_log:fixture.audit,revisit_chain:[],direct_revisits:[],active_chain:null,work_status:{applicable:false,state:'not_applicable',label:'Not applicable',reason:null}});}
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
// Disposable real component venue: no shipping route/provider/config mutation.
function prepareDrawer() {
  const scratch = resolve('web/.screenshot-harness-tmp/TASK-11445');
  mkdirSync(scratch, { recursive: true });
  const files = {
    'index.html': '<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="./drawer.tsx"></script></body></html>',
    'drawer.tsx': `import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { I18nProvider } from '@/hooks/i18n';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { OrgProvider } from '@/lib/orgSlug';
import { AgentDetailDrawer } from '@/features/agents/AgentDetailDrawer';
import { bootstrapDocumentLocale } from '@/lib/i18n';
import '@/styles.css';
const initialResolution = bootstrapDocumentLocale({mode:'full'});
document.documentElement.dataset.theme = localStorage.getItem('happyranch.theme') || 'light';
createRoot(document.getElementById('root')!).render(<I18nProvider mode="full" initialResolution={initialResolution}><AppProvider><BrowserRouter><Routes><Route path="/orgs/:slug/agents/:agentName" element={<OrgProvider><AgentDetailDrawer agentName="dev_agent" /></OrgProvider>} /></Routes></BrowserRouter></AppProvider></I18nProvider>);
`,
    'drawer.vite.config.mjs': `import { mergeConfig } from 'vite';
import config from '../../vite.config.ts';
import { fileURLToPath } from 'node:url';
import { renameSync } from 'node:fs';
import { join } from 'node:path';
export default mergeConfig(config, {plugins:[{name:'disposable-root-html',writeBundle(options,bundle){for(const key of Object.keys(bundle)){if(key.endsWith('/index.html'))renameSync(join(options.dir,key),join(options.dir,'index.html'));}}}],build:{rollupOptions:{input:fileURLToPath(new URL('./index.html',import.meta.url))}}});
`,
  };
  mkdirSync(join(out,'drawer-inputs'),{recursive:true});
  for (const [name, bytes] of Object.entries(files)) {
    writeFileSync(join(scratch,name),bytes);writeFileSync(join(out,'drawer-inputs',name),bytes);
  }
  writeFileSync(join(out,'drawer-inputs','manifest.json'),JSON.stringify(Object.entries(files).map(([file,bytes])=>({file,sha256:digest(bytes)})),null,2)+'\n');
  console.log(scratch);
}
async function main(){
  if(!existsSync(join(dist,'index.html')))throw new Error('ordinary production dist required');
  mkdirSync(out,{recursive:true});
  const venueDist=correctionOnly&&view==='drawer'?drawerDist:dist;
  if(!venueDist||!existsSync(join(venueDist,'index.html')))throw new Error('selected component dist required');
  const profile=mkdtempSync('/tmp/TASK-11445-pause-ui-');
  const blank=join(venueDist,'pause-locale-switch.html');if(existsSync(blank))throw new Error('refuse overwrite blank page');writeFileSync(blank,'<!doctype html><title>Isolated locale storage writer</title>');
  const server=await createServer({root:venueDist,api:[{path:/^\/api\//,handler:api}]});
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
    async function shot(page,name,metadata){await cdp.send('Page.bringToFront',{},page.sessionId);await sleep(100);const {data}=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},page.sessionId);const bytes=Buffer.from(data,'base64');const file=join(out,`${name}.png`);writeFileSync(file,bytes);screenshots.push({file,sha256:digest(bytes),source_head:head,dist_index_sha256:digest(readFileSync(join(venueDist,'index.html'))),...metadata});}
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
    if(!correctionOnly){
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
    // Horizontal-fit offenders for a header: the document, every clipping/scrolling ancestor (the app shell's <main> is
    // overflow-hidden and the page scrolls internally, so the document alone never sees in-page overflow), and every
    // rendered element's right edge. Returns [] when everything fits.
    const overflowOf=sel=>`((r)=>{const out=[];const d=document.documentElement.scrollWidth-innerWidth;if(d>1)out.push('document +'+d);for(let a=r.parentElement;a&&a!==document.documentElement;a=a.parentElement){if(getComputedStyle(a).overflowX!=='visible'&&a.scrollWidth>a.clientWidth+1)out.push(a.tagName.toLowerCase()+' scrollWidth +'+(a.scrollWidth-a.clientWidth));}for(const e of [r,...r.querySelectorAll('*')]){const b=e.getBoundingClientRect();if(b.width>0&&b.right>innerWidth+1)out.push(e.tagName.toLowerCase()+' "'+e.textContent.trim().slice(0,32)+'" right '+Math.round(b.right));}return out;})(${sel})`;
    const pressKey=async(page,key,code,vk)=>{await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode:vk,...(key==='Enter'?{text:'\r'}:{})},page.sessionId);await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:vk},page.sessionId);};
    for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]])for(const theme of ['light','dark']){
      scenario='populated';toggled={held:false,generation:4};const name=`${locale}-${width}-${theme}`;const mobile=width<768;
      const normal=await open('/orgs/pause-org/tasks/ROOT-RUN',locale,width,height,theme);await wait(normal,`!!${rootButton('ROOT-RUN',tr(locale,'tasks.pause.pause'))}`);await sleep(150);
      check(`${name} seq77 normal: region is only the compact Pause`,await evaluate(normal,`${region('ROOT-RUN')}.textContent.trim()`),tr(locale,'tasks.pause.pause'));
      check(`${name} seq77 normal: Pause shares the row with Revisit/Cancel/audit`,await evaluate(normal,`(()=>{const row=${region('ROOT-RUN')}.parentElement;return [${quote(tr(locale,'tasks.detail.revisit'))},${quote(tr(locale,'tasks.detail.cancel'))}].every(n=>[...row.querySelectorAll('button')].some(b=>b.textContent.trim()===n))&&!!row.querySelector('a[href="/orgs/pause-org/audit?task_id=ROOT-RUN"]')&&!row.querySelector('details');})()`));
      check(`${name} seq77 normal: one visual row`,await evaluate(normal,`(()=>{const p=${rootButton('ROOT-RUN',tr(locale,'tasks.pause.pause'))}.getBoundingClientRect(),r=${button(tr(locale,'tasks.detail.revisit'))}.getBoundingClientRect();return Math.abs((p.top+p.bottom)/2-(r.top+r.bottom)/2)<3;})()`));
      check(`${name} seq77 normal fits`,await evaluate(normal,overflowOf(`${region('ROOT-RUN')}.closest('header')`)),[]);
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
      check(`${name} seq77 pausing open: list rows within viewport`,await evaluate(drain,`[${region('ROOT-DRAINING')}.querySelectorAll('li').length,[...${region('ROOT-DRAINING')}.querySelectorAll('li,li *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>e.tagName.toLowerCase()+' '+Math.round(e.getBoundingClientRect().right)),${overflowOf(`${region('ROOT-DRAINING')}.closest('header')`)}]`),[3,[],[]]);
      if(mobile){const o=await evaluate(drain,`({list:${rect(`${region('ROOT-DRAINING')}.querySelector('ul')`)},rev:${rect(button(tr(locale,'tasks.detail.revisit')))}})`);check(`${name} seq77 pausing mobile open: list pushes ordinary actions down (document flow)`,o.list.bottom<=o.rev.top+1);}
      await shot(drain,`${name}-seq77-pausing-open`,{locale,theme,width,height,state:'seq77 pausing, disclosure open via keyboard'});
      await pressKey(drain,'Enter','Enter',13);await sleep(120);
      check(`${name} seq77 pausing: Enter closes disclosure`,await evaluate(drain,`${region('ROOT-DRAINING')}.querySelector('details').open`),false);
      await close(drain);
      const paused=await open('/orgs/pause-org/tasks/ROOT-PAUSED',locale,width,height,theme);await wait(paused,`!!${rootButton('ROOT-PAUSED',tr(locale,'tasks.pause.resume'))}`);await sleep(150);
      check(`${name} seq77 paused: primary Resume`,await evaluate(paused,`/\\bbg-primary\\b/.test((${rootButton('ROOT-PAUSED',tr(locale,'tasks.pause.resume'))}).className)`));
      check(`${name} seq77 paused: distinct Paused indicator, no disclosure`,await evaluate(paused,`(()=>{const r=${region('ROOT-PAUSED')};return r.querySelector('span.rounded-pill').textContent.trim()===${quote(tr(locale,'tasks.pause.paused'))}&&!r.querySelector('details');})()`));
      check(`${name} seq77 paused: indicator contrast >= 4.5`,await evaluate(paused,`${contrastOf(`${region('ROOT-PAUSED')}.querySelector('span.rounded-pill')`)}>=4.5`));
      check(`${name} seq77 paused fits`,await evaluate(paused,overflowOf(`${region('ROOT-PAUSED')}.closest('header')`)),[]);
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
    }
    if(correctionOnly && slice==='agents') {
      // Wrong selections are HTTP negative observations, not backend authority proof.
      for(const query of ['assigned_agent=other&limit=20','assigned_agent=dev_agent&limit=19','assigned_agent=dev_agent']) {
        const response=await fetch(server.url+'/api/v1/orgs/pause-org/tasks?'+query);check('R1a wrong selection '+query,response.status,400);
      }
      check('R1a wrong org',(await fetch(server.url+'/api/v1/orgs/wrong/tasks?assigned_agent=dev_agent&limit=20')).status,400);
      for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]]) {
        const theme=width===390?'light':'dark';const name=`${view}-${locale}-${width}-${theme}`;
        const start=ledger.length;const page=await open('/orgs/pause-org/agents/dev_agent',locale,width,height,theme);
        await wait(page,"!!document.querySelector('a[href=\"/orgs/pause-org/tasks/UI-CARD-11\"]')");await sleep(350);
        const card=id=>`document.querySelector('a[href="/orgs/pause-org/tasks/UI-CARD-${id}"]')`;
        const expected=locale==='en'?['Continuation pending','Child work pending','Waiting on child work','Child results received; continuation pending']:['等待后续处理','子任务待执行','等待子任务处理','已收到子任务结果，等待后续处理'];
        for(let i=0;i<4;i++)check(`${name} A10 state ${i}`,await evaluate(page,`${card(i)}.textContent.includes(${quote('· '+expected[i])})`));
        for(let i=4;i<9;i++)check(`${name} A10 neutral ${i}`,await evaluate(page,`${card(i)}.querySelector('span.font-normal.opacity-70')===null`));
        check(`${name} job preserved`,await evaluate(page,`${card(9)}.textContent.includes(${quote('· '+tr(locale,'tasks.waiting.jobs'))})`));
        for(const i of [10,11])check(`${name} worse rollup ${i}`,await evaluate(page,`${card(i)}.querySelector('span.font-normal.opacity-70')===null`));
        check(`${name} no pause/card disclosure`,await evaluate(page,`!document.querySelector('[data-task-pause]')&&![...document.querySelectorAll('button')].some(b=>['Pause','Resume','暂停','恢复'].includes(b.textContent.trim()))&&!document.querySelector('details')`));
        check(`${name} no overview read`,ledger.slice(start).filter(x=>x.path.endsWith('/pause-overview')).length,0);
        check(`${name} actual selected request/payload`,ledger.slice(start).filter(x=>x.agentTasks&&x.status===200).map(x=>[x.path,new URLSearchParams(x.search).get('assigned_agent'),new URLSearchParams(x.search).get('limit'),x.payload.tasks.map(t=>t.task_id)]),[['/api/v1/orgs/pause-org/tasks','dev_agent','20',agentTasks().map(t=>t.task_id)]]);
        if(view==='drawer')check(`${name} drawer effective clamp`,await evaluate(page,`(()=>{const r=document.querySelector('[role="dialog"]').getBoundingClientRect();return [Math.round(r.left),Math.round(r.right),Math.round(r.width)];})()`),[width===390?0:792,width,width===390?390:648]);
        // Capture real card regions after scrolling. Check both bounds and clipping ancestors.
        for(const i of [0,1,4,5,6,7,8]) {
          await evaluate(page,`${card(i)}.scrollIntoView({block:'center'})`);await sleep(70);
          const bounds=await evaluate(page,`(()=>{const e=${card(i)},r=e.getBoundingClientRect(),bad=[];for(const n of [e,...e.querySelectorAll('*')]){const b=n.getBoundingClientRect();if(b.width>0&&(b.left< -1||b.right>innerWidth+1))bad.push(n.tagName+':'+Math.round(b.right));}for(let a=e.parentElement;a;a=a.parentElement){if(getComputedStyle(a).overflowX!=='visible'&&a.scrollWidth>a.clientWidth+1)bad.push('clip:'+a.tagName);}return {bad,visible:r.top>=-1&&r.bottom<=innerHeight+1,top:r.top,bottom:r.bottom};})()`);
          check(`${name} card ${i} horizontal bounds`,bounds.bad,[]);check(`${name} card ${i} reachable`,bounds.visible);
          await shot(page,`${name}-card${i}`,{case:'A10/U01',view,locale,width,height,theme,state:agentStates[i],bounds,venue: view==='drawer'?'real component/providers':'shipping route'});
        }
        await evaluate(page,`(()=>{${card(0)}.scrollIntoView({block:'center'});${card(0)}.focus();return true;})()`);
        await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9},page.sessionId);
        await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9},page.sessionId);
        check(`${name} keyboard card focus`,await evaluate(page,`(()=>{window.selectedCard=document.activeElement;return selectedCard===${card(1)}&&selectedCard.matches(':focus-visible');})()`));
        for(const next of [locale==='en'?'zh-CN':'en',locale]) {
          const from=ledger.length;await switchLocale(page,next);
          check(`${name}->${next} same focused card`,await evaluate(page,`selectedCard===${card(1)}&&document.activeElement===selectedCard`));
          check(`${name}->${next} zero locale HTTP`,ledger.slice(from),[]);
          check(`${name}->${next} translated same card`,await evaluate(page,`selectedCard.textContent.includes(${quote(next==='en'?'Child work pending':'子任务待执行')})`));
        }
        await shot(page,`${name}-focus`,{case:'U01',view,locale,width,height,theme,state:'focused actual task link'});
        await close(page);
      }
    }
    if(correctionOnly && slice==='detail') {
      const cases=['UI-A01','UI-A02','UI-A04','UI-A07-1','UI-A05-1','UI-A07-2','UI-A07-3','UI-A08-empty','UI-A08-legacy','UI-A08-malformed',...['in_progress','completed','failed','cancelled','superseded'].map(s=>'UI-A09-'+s),...['missing-id','duplicate-id','malformed-id','missing-node','duplicate-node','wrong-root','null-node','bad-status','missing-recall','width','missing-width','empty-ids','zero-width','mismatch-width','loading','error'].flatMap(s=>['UI-A08-bad-'+s,'UI-A08-joined-bad-'+s])];
      for(const locale of selectedLocales) {
        const width=locale==='en'?1440:390,height=locale==='en'?900:844,theme=locale==='en'?'dark':'light';
        const list=await open('/orgs/pause-org/tasks',locale,width,height,theme);await wait(list,"!!document.querySelector('a[href=\"/orgs/pause-org/tasks/UI-A01\"]')");
        check(`${locale} A01 list continuation`,await evaluate(list,`document.querySelector('a[href="/orgs/pause-org/tasks/UI-A01"]').textContent.includes(${quote(tr(locale,'tasks.waiting.continuationPending'))})`));
        check(`${locale} list pause free`,await evaluate(list,listPauseFree));await shot(list,`${locale}-correction-list`,{case:'A01/A10',locale,width,height,theme,state:'ordinary list'});await close(list);
        for(const id of cases) {
          const fixture=correctionFixture(id);const page=await open('/orgs/pause-org/tasks/'+id,locale,width,height,theme);
          await wait(page,`!!document.querySelector('h1')&&document.body.textContent.includes(${quote(fixture.task.brief)})`);await sleep(80);
          const actual=await evaluate(page,"({header:document.querySelector('header')?.textContent,band:document.querySelector('section[aria-label=\"'+(document.documentElement.lang==='en'?'Fan-out status':'扇出状态')+'\"]')?.textContent,bar:document.querySelector('section[aria-label] [role=img]')?.getAttribute('aria-label'),recall:document.body.textContent})");
          if(id.includes('A07')||id.includes('A05')) {
            const state=fixture.task.child_wait.state;const keys={waiting_children:'waitingChildren',child_results_received:'resultsReceived',continuation_pending:'continuationPending'};
            check(`${locale} ${id} parent projection headline`,actual.band?.includes(tr(locale,'tasks.fanout.'+keys[state])));
            const expected=id==='UI-A07-2'? (locale==='en'?'Terminal child rows: 0/2 · 1 pending row · 1 escalated row':'子任务终止状态：0/2 · 待处理 1 项 · 已升级 1 项') : id==='UI-A07-3'?(locale==='en'?'Terminal child rows: 4/4 · 1 completed row · 1 failed row · 1 cancelled row · 1 superseded row':'子任务终止状态：4/4 · 已完成 1 项 · 失败 1 项 · 已取消 1 项 · 已被取代 1 项'):(locale==='en'?'Terminal child rows: 1/2 · 1 in-progress row · 1 completed row':'子任务终止状态：1/2 · 生命周期进行中 1 项 · 已完成 1 项');
            check(`${locale} ${id} literal summary`,actual.band?.includes(expected));check(`${locale} ${id} accessible bar agrees`,actual.bar,expected);
            check(`${locale} ${id} bar proportions`,await evaluate(page,`[...document.querySelector('section[aria-label] [role=img]').children].map(e=>parseFloat(e.style.width))`),id==='UI-A07-3'?[25,25,25,25]:[50,50]);
          }
          if(id.startsWith('UI-A09-')){check(`${locale} ${id} historical join`,actual.band?.includes(tr(locale,'tasks.fanout.joined')));check(`${locale} ${id} independent parent`,actual.header?.includes(fixture.task.status));check(`${locale} ${id} no resumed inference`,!/Parent resumed|父任务已恢复|succeeded/.test(actual.band));}
          if(/^UI-A08-(joined-)?bad-/.test(id)){check(`${locale} ${id} unavailable counts`,actual.band?.includes(tr(locale,'tasks.fanout.countsUnavailable')));check(`${locale} ${id} no numeric bar`,actual.bar??null,null);check(`${locale} ${id} no summary/width`,!/Terminal child rows|子任务终止状态|Width |宽度 /.test(actual.band));}
          if(id==='UI-A01'){
            await evaluate(page,"document.querySelector('[data-task-pause] summary').focus()");
            await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r'},page.sessionId);
            await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13},page.sessionId);
            check(`${locale} U01 keyboard disclosure`,await evaluate(page,"document.querySelector('[data-task-pause] details').open"));
            check(`${locale} B01 unknown job cleanup`,await evaluate(page,`document.querySelector('[data-task-pause]').textContent.includes(${quote(tr(locale,'tasks.pause.blocker.jobCleanup'))})`));
            check(`${locale} B01 no running inference`,await evaluate(page,"!/still running|仍在运行/.test(document.querySelector('[data-task-pause] summary').textContent)"));
            const fits=await evaluate(page,"[...document.querySelectorAll('[data-task-pause] li,[data-task-pause] li *')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.left< -1||r.right>innerWidth+1);}).map(e=>e.textContent)");
            check(`${locale} U01 long raw IDs fit`,fits,[]);
            await shot(page,`${locale}-B01-long-id`,{case:'B01/U01',locale,width,height,theme,state:'expanded actual raw ID'});
            await evaluate(page,`(()=>{const p=[...document.querySelectorAll('[data-task-pause] p')].find(e=>e.textContent===${quote(tr(locale,'tasks.pause.jobCleanupNote'))});p.scrollIntoView({block:'center'});return true;})()`);
            await shot(page,`${locale}-B01-note`,{case:'B01/U01',locale,width,height,theme,state:'cleanup-unconfirmed note reached by actual scrolling'});

          }
          if(id==='UI-A02')check(`${locale} A02 separate Paused`,actual.header?.includes(tr(locale,'tasks.pause.paused')));
          if(['UI-A08-empty','UI-A08-legacy','UI-A08-malformed'].includes(id))check(`${locale} ${id} no false subtasks`,!/waiting on subtasks|· 等待子任务$/.test(actual.header));
          const controls=ledger.filter(x=>x.method!=='GET'&&!x.path.endsWith('/auth/bootstrap'));check(`${locale} ${id} read only transcript`,controls,[]);
          if(id==='UI-A01')await evaluate(page,"document.querySelector('h1').scrollIntoView({block:'start'})");
          if(!/^UI-A08-(joined-)?bad-/.test(id)||id.endsWith('null-node')||id==='UI-A08-joined-bad-width')await shot(page,`${locale}-${id}`,{case:id,locale,width,height,theme,state:fixture.task.child_wait??'legacy',actual});
          await close(page);
        }
        for(const id of ['CHILD-1','ROOT-PENDING','ROOT-FAILED','ROOT-RUN']) {
          const page=await open('/orgs/pause-org/tasks/'+id,locale,width,height,theme);
          await wait(page,`!!document.querySelector('[data-task-pause="${id}"]')||document.body.textContent.includes('Authored ${id}')`);
          const region=`document.querySelector('[data-task-pause="${id}"]')`;
          if(id==='CHILD-1'){check(`${locale} A11 inherited root only`,await evaluate(page,`${region}.querySelector('a')?.getAttribute('href')`),'/orgs/pause-org/tasks/ROOT-PAUSING');check(`${locale} A11 no child control`,await evaluate(page,`${region}.querySelector('button')===null`));}
          if(id==='ROOT-PENDING'||id==='ROOT-FAILED')check(`${locale} A09/A11 no terminal/pending control`,await evaluate(page,`${region}?.querySelector('button')??null`),null);
          await shot(page,`${locale}-${id}`,{case:'A09/A11',locale,width,height,theme,state:id});await close(page);
        }
      }
    }
    check('zero GET /tasks/pause-overview across the whole run',overviewReads(),0);
    const assets=readdirSync(join(dist,'assets')).filter(file=>/\.(js|css)$/.test(file)).map(file=>({file,sha256:digest(readFileSync(join(dist,'assets',file)))}));
    const receipt={boundary:'UI/transport only; synthetic HTTP, not backend enforcement/E2E',head,generated_at:new Date().toISOString(),node:{path:process.execPath,version:process.version},chrome:{path:chromeBin,...browser},dist:{path:dist,index_sha256:digest(readFileSync(join(dist,'index.html'))),assets},venue:{view,path:venueDist,index_sha256:digest(readFileSync(join(venueDist,'index.html'))),assets:readdirSync(join(venueDist,'assets')).filter(f=>/\.(js|css)$/.test(f)).map(file=>({file,sha256:digest(readFileSync(join(venueDist,'assets',file)))}))},origin:server.url,profile,agentReads,checks,contrast,screenshots,ledger,browserEvents,failed:checks.filter(c=>!c.pass).length};writeFileSync(join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
    console.log(`${checks.length} browser assertions; ${screenshots.length} screenshots; ${receipt.failed} failures; UI/transport ONLY`);process.exitCode=receipt.failed?1:0;
  }catch(error){writeFileSync(join(out,'failure.json'),JSON.stringify({head,boundary:'UI/transport only',error:error.stack,checks,screenshots,ledger,browserEvents},null,2)+'\n');throw error;}
  finally{for(const response of hung)response.destroy();cdp?.ws.close();if(chrome.exitCode===null&&chrome.signalCode===null)chrome.kill('SIGTERM');await new Promise(resolve=>{if(chrome.exitCode!==null||chrome.signalCode!==null)resolve();else{chrome.once('exit',resolve);setTimeout(()=>{chrome.kill('SIGKILL');resolve();},1000).unref();}});writeFileSync(join(out,'chrome-stderr.log'),Buffer.concat(chromeErrors));server.server.closeAllConnections?.();await server.close();try{rmSync(profile,{recursive:true,force:true,maxRetries:3,retryDelay:100});}catch(error){writeFileSync(join(out,'cleanup-failure.json'),JSON.stringify({profile,error:String(error)})+'\n');process.exitCode=1;}rmSync(blank);}
}
if(process.argv.includes('--prepare-drawer')) prepareDrawer();
else main().catch(error=>{console.error(error.stack);process.exitCode=1;});
