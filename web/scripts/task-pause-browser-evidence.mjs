#!/usr/bin/env node
/**
 * TASK-10560 P18/G10: ordinary-build UI/transport evidence only.
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
const ledger = [], checks = [], screenshots = [], hung = [];
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
    root('ROOT-ESCALATED','escalated',{pause:projection('ROOT-ESCALATED','escalated',{held:true,effective_hold:true,control_state:'paused'})}),
    root('ROOT-FAILED','failed',{pause:projection('ROOT-FAILED','failed',{held:true,blockers:[blockers[4]],evidence_complete:false})}),
    root('ROOT-MISSING','in_progress',{pause:undefined})];
}
function overview(before) {
  const page={org_slug:'pause-org',observed_at:before?'2026-10-11T01:00:00Z':stamp,evidence_complete:false,counts:null,next_cursor:null,unheld_runnable:[],pausing:[],paused:[],terminal_drain:[],unavailable_roots:[]};
  if(scenario==='empty') return {...page,evidence_complete:true,counts:{unheld_runnable:0,pausing:0,paused:0,terminal_drain:0,unavailable_roots:0}};
  if(before) return {...page,unheld_runnable:[projection('SEPARATE-SUCCESSOR','pending')]};
  for(const task of roots()) {
    if(!task.pause) continue;
    const p=task.pause;
    if(task.status==='failed') page.terminal_drain.push(p);
    else if(p.effective_hold) page[p.control_state].push(p);
    else if(task.status==='pending'||task.status==='in_progress') page.unheld_runnable.push(p);
  }
  page.unavailable_roots=[projection('ROOT-UNAVAILABLE','pending',{evidence_complete:false})];
  if(scenario==='partial') page.next_cursor='opaque:/+cursor=';
  return page;
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
  if(url.pathname.endsWith('/tasks/roots')) return end({tasks:url.searchParams.get('status')==='escalated'?roots().filter(t=>t.status==='escalated'):roots(),next_cursor:null});
  if(url.pathname.endsWith('/pause-overview')) {
    if(scenario==='loading') await sleep(750);
    if(scenario==='error'||scenario==='stale') return end({detail:{code:'pause_control_unavailable'}},409);
    return end(overview(url.searchParams.get('before')));
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
  constructor(url){this.ws=new WebSocket(url);this.id=0;this.pending=new Map();this.ready=new Promise((ok,fail)=>{this.ws.onopen=ok;this.ws.onerror=()=>fail(new Error('CDP connection failed'));});this.ws.onmessage=event=>{const message=JSON.parse(event.data);const pending=this.pending.get(message.id);if(pending){this.pending.delete(message.id);message.error?pending.reject(new Error(JSON.stringify(message.error))):pending.resolve(message.result);}};}
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
    async function wait(page,expression){const until=Date.now()+5000;while(Date.now()<until){if(await evaluate(page,expression))return;await sleep(40);}const state=await evaluate(page,'({url:location.href,title:document.title,text:document.body?.textContent?.slice(0,1200)})');throw new Error(`UI wait timed out: ${expression}; ${JSON.stringify(state)}`);}
    async function open(route,locale='en',width=1440,height=900,theme='light'){
      const {targetId}=await cdp.send('Target.createTarget',{url:'about:blank'});const {sessionId}=await cdp.send('Target.attachToTarget',{targetId,flatten:true});const page={targetId,sessionId};
      await cdp.send('Page.enable',{},sessionId);await cdp.send('Runtime.enable',{},sessionId);await cdp.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false},sessionId);
      await cdp.send('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.setItem('happyranch.ui.locale',${quote(locale)});localStorage.setItem('happyranch.theme',${quote(theme)});`},sessionId);
      await cdp.send('Page.navigate',{url:server.url+route},sessionId);return page;
    }
    const close=page=>cdp.send('Target.closeTarget',{targetId:page.targetId});
    async function shot(page,name,metadata){await sleep(100);const {data}=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},page.sessionId);const bytes=Buffer.from(data,'base64');const file=join(out,`${name}.png`);writeFileSync(file,bytes);screenshots.push({file,sha256:digest(bytes),...metadata});}
    const rootButton=(id,label)=>`[...document.querySelectorAll('[data-tasks-responsive-list] [data-task-pause="${id}"] button')].find(b=>b.textContent.trim()===${quote(label)})`;
    const button=label=>`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${quote(label)})`;
    async function enter(page,source){await evaluate(page,`(()=>{const button=${source};if(!button)throw new Error('button missing');button.scrollIntoView({block:'center'});button.focus();return true;})()`);for(const type of ['keyDown','keyUp'])await cdp.send('Input.dispatchKeyEvent',{type,key:'Enter',code:'Enter',windowsVirtualKeyCode:13},page.sessionId);}
    async function switchLocale(page,locale){
      const {targetId}=await cdp.send('Target.createTarget',{url:server.url+'/pause-locale-switch.html'});const {sessionId}=await cdp.send('Target.attachToTarget',{targetId,flatten:true});await cdp.send('Runtime.enable',{},sessionId);await sleep(60);
      await evaluate({sessionId},`localStorage.setItem('happyranch.ui.locale',${quote(locale)})`);await cdp.send('Target.closeTarget',{targetId});await wait(page,`document.documentElement.lang===${quote(locale)}`);await sleep(100);
    }
    for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]])for(const theme of ['light','dark']){
      scenario='populated';toggled={held:false,generation:4};const name=`${locale}-${width}-${theme}`;
      const page=await open('/orgs/pause-org/tasks',locale,width,height,theme);await wait(page,"!!document.querySelector('[data-tasks-responsive-list] [data-task-pause=\"ROOT-RUN\"]')");await wait(page,`document.body.textContent.includes(${quote(tr(locale,'tasks.pause.overview.title'))}) && !document.body.textContent.includes(${quote(tr(locale,'tasks.pause.overview.loading'))})`);
      check(`${name} html locale`,await evaluate(page,'document.documentElement.lang'),locale);
      check(`${name} theme`,await evaluate(page,'document.documentElement.dataset.theme'),theme);
      check(`${name} page fits`,await evaluate(page,'document.documentElement.scrollWidth <= innerWidth + 1'));
      check(`${name} pending has no Pause`,await evaluate(page,"document.querySelector('[data-tasks-responsive-list] [data-task-pause=\"ROOT-PENDING\"] button')===null"));
      check(`${name} terminal has no control`,await evaluate(page,"document.querySelector('[data-tasks-responsive-list] [data-task-pause=\"ROOT-FAILED\"] button')===null"));
      check(`${name} typed unknown shown`,await evaluate(page,`document.body.textContent.includes(${quote(tr(locale,'tasks.pause.blocker.unknown'))})`));
      // Open blocker details and verify control bounds through actual browser geometry.
      await evaluate(page,"document.querySelectorAll('[data-task-pause] details').forEach(d=>d.open=true)");
      check(`${name} control widths`,await evaluate(page,"[...document.querySelectorAll('[data-task-pause] button,[data-task-pause] summary')].every(b=>{const r=b.getBoundingClientRect();return r.left>=-1&&r.right<=innerWidth+1;})"));
      await shot(page,`${name}-list`,{locale,theme,width,height,state:'populated/partial/terminal/unknown'});
      await close(page);
      const detail=await open('/orgs/pause-org/tasks/ROOT-PAUSING',locale,width,height,theme);await wait(detail,"!!document.querySelector('[data-task-pause] button')");
      check(`${name} detail Resume during drain`,await evaluate(detail,`document.querySelector('[data-task-pause] button').textContent.trim()`),tr(locale,'tasks.pause.resume'));
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
      check(`${name} Cancel focus return`,await evaluate(detail,`document.activeElement.textContent.trim()`),tr(locale,'tasks.detail.cancel'));
      await close(detail);
    }
    // Actual UI actions, intercepted shipping client requests and refresh receipts.
    scenario='populated';toggled={held:false,generation:4};const page=await open('/orgs/pause-org/tasks');await wait(page,`!!${rootButton('ROOT-RUN','Pause')}`);
    let from=ledger.length;await enter(page,rootButton('ROOT-RUN','Pause'));
    await wait(page,`(${rootButton('ROOT-RUN','Pause')})?.disabled===true`);
    check('Pause disables repeats',await evaluate(page,`(${rootButton('ROOT-RUN','Pause')})?.disabled`));
    await wait(page,`!!${rootButton('ROOT-RUN','Resume')} && !(${rootButton('ROOT-RUN','Resume')}).disabled`);
    check('Pause one shipping POST',ledger.slice(from).filter(x=>x.method==='POST').map(x=>[x.path,x.body]),[['/api/v1/orgs/pause-org/tasks/ROOT-RUN/pause',{expected_generation:4}]]);
    check('Pause refreshed list and overview',ledger.slice(from).some(x=>x.path.endsWith('/tasks/roots'))&&ledger.slice(from).some(x=>x.path.endsWith('/pause-overview')));
    from=ledger.length;await enter(page,rootButton('ROOT-RUN','Resume'));await wait(page,`!!${rootButton('ROOT-RUN','Pause')} && !(${rootButton('ROOT-RUN','Pause')}).disabled`);
    check('Resume one shipping POST',ledger.slice(from).filter(x=>x.method==='POST').map(x=>x.body),[{expected_generation:5}]);
    control='conflict';from=ledger.length;await enter(page,rootButton('ROOT-RUN','Pause'));await wait(page,"!!document.querySelector('[data-tasks-responsive-list] [role=\"alert\"]')");await sleep(100);
    check('Conflict no opposing/retry POST',ledger.slice(from).filter(x=>x.method==='POST').map(x=>[x.path,x.body,x.status]),[['/api/v1/orgs/pause-org/tasks/ROOT-RUN/pause',{expected_generation:6},409]]);
    check('Conflict accessible translated copy',await evaluate(page,`document.querySelector('[data-tasks-responsive-list] [role="alert"]').textContent.includes(${quote(tr('en','tasks.pause.error.conflict'))})`));
    await close(page);
    // Independent finite observation states, plus pagination and child inherited hold.
    for(const locale of ['en','zh-CN'])for(const [width,height]of [[390,844],[1440,900]])for(const theme of ['light','dark'])for(const mode of ['loading','empty','error','partial']){
      scenario=mode;const page=await open('/orgs/pause-org/tasks',locale,width,height,theme);
      const key=mode==='loading'?'tasks.pause.overview.loading':mode==='empty'?'tasks.pause.overview.empty':mode==='error'?'tasks.pause.overview.error':'tasks.pause.overview.partial';
      await wait(page,`document.body.textContent.includes(${quote(tr(locale,key))})`);check(`overview ${locale} ${width} ${theme} ${mode} localized`,true);await shot(page,`${locale}-${width}-${theme}-${mode}`,{locale,width,height,theme,state:mode});
      if(mode==='partial'){await enter(page,button(tr(locale,'tasks.pause.overview.next')));await wait(page,"document.body.textContent.includes('SEPARATE-SUCCESSOR')");check('opaque cursor preserved',ledger.some(x=>x.path.endsWith('/pause-overview')&&new URLSearchParams(x.search).get('before')==='opaque:/+cursor='));check('later page is not summed',await evaluate(page,`![...document.querySelectorAll('section')].find(s=>s.getAttribute('aria-label')===${quote(tr(locale,'tasks.pause.overview.title'))}).textContent.includes('ROOT-PAUSING')`));}
      await close(page);
    }
    scenario='populated';const stale=await open('/orgs/pause-org/tasks');await wait(stale,`!!${button(tr('en','tasks.pause.overview.refresh'))} && !(${button(tr('en','tasks.pause.overview.refresh'))}).disabled`);scenario='stale';await enter(stale,button(tr('en','tasks.pause.overview.refresh')));await wait(stale,`document.body.textContent.includes(${quote(tr('en','tasks.pause.overview.stale'))})`);await shot(stale,'en-1440-light-stale',{locale:'en',width:1440,height:900,theme:'light',state:'stale'});await close(stale);
    const child=await open('/orgs/pause-org/tasks/CHILD-1','zh-CN',390,844);await wait(child,"!!document.querySelector('[data-task-pause=\"CHILD-1\"]')");check('child has inherited root link',await evaluate(child,"document.querySelector('[data-task-pause] a').getAttribute('href')"),'/orgs/pause-org/tasks/ROOT-PAUSING');check('child has no controls',await evaluate(child,"document.querySelector('[data-task-pause] button')===null"));await shot(child,'zh-390-light-child',{locale:'zh-CN',width:390,height:844,theme:'light',state:'child inherited'});await close(child);
    const assets=readdirSync(join(dist,'assets')).filter(file=>/\.(js|css)$/.test(file)).map(file=>({file,sha256:digest(readFileSync(join(dist,'assets',file)))}));
    const receipt={boundary:'UI/transport only; synthetic HTTP, not backend enforcement/E2E',head,generated_at:new Date().toISOString(),node:{path:process.execPath,version:process.version},chrome:{path:chromeBin,...browser},dist:{path:dist,index_sha256:digest(readFileSync(join(dist,'index.html'))),assets},checks,screenshots,ledger,failed:checks.filter(c=>!c.pass).length};writeFileSync(join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
    console.log(`${checks.length} browser assertions; ${screenshots.length} screenshots; ${receipt.failed} failures; UI/transport ONLY`);process.exitCode=receipt.failed?1:0;
  }catch(error){writeFileSync(join(out,'failure.json'),JSON.stringify({head,boundary:'UI/transport only',error:error.stack,checks,screenshots,ledger},null,2)+'\n');throw error;}
  finally{for(const response of hung)response.destroy();cdp?.ws.close();if(chrome.exitCode===null&&chrome.signalCode===null)chrome.kill('SIGTERM');await new Promise(resolve=>{if(chrome.exitCode!==null||chrome.signalCode!==null)resolve();else{chrome.once('exit',resolve);setTimeout(()=>{chrome.kill('SIGKILL');resolve();},1000).unref();}});writeFileSync(join(out,'chrome-stderr.log'),Buffer.concat(chromeErrors));server.server.closeAllConnections?.();await server.close();try{rmSync(profile,{recursive:true,force:true,maxRetries:3,retryDelay:100});}catch(error){writeFileSync(join(out,'cleanup-failure.json'),JSON.stringify({profile,error:String(error)})+'\n');process.exitCode=1;}rmSync(blank);}
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
