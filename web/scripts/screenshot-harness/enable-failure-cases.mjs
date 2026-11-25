// Case adapter for the existing screenshot harness. Synthetic native IO only.
// Run from web after locked dependencies: node scripts/screenshot-harness/enable-failure-cases.mjs TRACE OUTPUT
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { startViteHarness, capture } from './harness.mjs';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const [tracePath, output] = process.argv.slice(2);
if (!tracePath || !output) throw new Error('Provide the produced trace and task output directory');
const trace = JSON.parse(await readFile(resolve(tracePath), 'utf8'));
const sourceSha = execFileSync('git', ['rev-parse', 'HEAD'], {cwd:web,encoding:'utf8'}).trim();
const traceSha = createHash('sha256').update(await readFile(resolve(tracePath))).digest('hex');
const reasons = [
  ['deb-layout-required','This installation does not support turning on remote access.','当前安装方式不支持开启远程访问。'],
  ['issuer-state-uncertain','The device invitation service could not be prepared safely.','无法安全准备设备邀请服务。'],
  ['account-shape','The service account does not match the required setup.','服务账户不符合所需配置。'],
  ['account-receipt-uncertain','The service account ownership record could not be verified.','无法验证服务账户的所有权记录。'],
  ['foreign-unit','An existing service configuration is not owned by HappyRanch.','现有服务配置不属于 HappyRanch。'],
  ['foreign-custody','Existing remote access files are not owned by HappyRanch.','现有远程访问文件不属于 HappyRanch。'],
  ['publication-uncertain','The remote access configuration could not be verified.','无法验证远程访问配置。'],
  ['address-changed','The home network address has changed since setup.','家庭网络地址在设置后发生了变化。'],
  ['account-create-uncertain','The service account could not be created and verified.','无法创建并验证服务账户。'],
  ['cleanup-uncertain','Existing remote access services could not be confirmed stopped.','无法确认现有远程访问服务已停止。'],
  ['materializer-uncertain','The secure certificate setup could not be verified.','无法验证安全证书配置。'],
  ['custody-uncertain','Safe ownership of remote access files could not be verified.','无法验证远程访问文件的安全所有权。'],
  ['inventory','The home network routes could not be verified.','无法验证家庭网络路由。'],
  ['no_address','No usable home network address was found.','未找到可用的家庭网络地址。'],
  ['multiple_addresses','More than one home network address was found.','找到多个家庭网络地址。'],
  ['dial_ip','The home network address is not supported.','不支持此家庭网络地址。'],
  ['dial-ip','The home network address is not supported.','不支持此家庭网络地址。'],
  ['occupied-port','A network port needed for remote access is already in use.','远程访问所需的网络端口已被占用。'],
  ['port-check-uncertain','The network ports needed for remote access could not be checked.','无法检查远程访问所需的网络端口。'],
];
if (!trace.relayEvent || JSON.parse(trace.relayEvent).error?.category !== 'inventory') throw new Error('Missing produced relay trace');
const scratch = join(web, '.screenshot-harness-tmp');
await mkdir(scratch, { recursive: true });
// This generated entry is test-side composition of the real dialog/providers.
// It supplies only native IO; real parsers, read hooks, model and JSX run.
const source = `
import React, {useEffect, useRef, useState} from 'react';
import {I18nProvider, useTranslation} from '@/hooks/i18n';
import {OwnerGateProvider} from '@/shared/owners';
import {ConnectionDialogBody} from '@/features/connection/ConnectionDialogBody';
import {Dialog, DialogContent} from '@/design-system/primitives/Dialog';
import {__setNativeBridgeForTests} from '@/lib/native/target';
const trace = __TRACE__;
const params = new URLSearchParams(location.search);
const listeners = new Map();
const off = {setup:'off',runtime:'offline',observation:'known',devices:[],pending:null,selected:null,inviteState:'none',expiresAt:null};
const unavailable = {...off,setup:'unknown',runtime:'unknown',observation:'unavailable',devices:null};
const ready = {...off,setup:'ready',runtime:'available'};
const admission = {...trace.nativeAdmission};
const api = (globalThis as any).enableCase = {
  calls:[], after:'off', kind:'failed', category:'inventory', terminal:false, dispatched:false, hold:false, reads:[],
  emit(){
    const frame = JSON.parse(trace.relayEvent);
    frame.operationId = admission.operationId;
    if(this.kind==='completed') {frame.outcome='completed';delete frame.error;
      frame.result={code:'enableCompleted',effect:'unknown',recovery:'refresh'};}
    else frame.error.category=this.category;
    this.terminal=true;
    for(const fn of listeners.get('host.enableFinished')??[]) fn(JSON.stringify(frame));
  },
  snapshot(){return this.after==='ready'?ready:this.after==='unavailable'?unavailable:off;},
  resolveRead(value,index=0){const item=this.reads.splice(index,1)[0]; if(!item)throw Error('No held status read');
    value==='throw'?item.reject('synthetic transport loss'):item.resolve(JSON.stringify(value==='ready'?ready:value==='off'?off:unavailable));},
};
__setNativeBridgeForTests({
  async call(command,data){
    if(data!=='{}' && command!=='app.ownersChanged')throw Error('Unexpected input');
    api.calls.push({command,terminal:api.terminal});
    if(command==='app.status')return JSON.stringify({schema:2,generation:'g1',
      target:{kind:'local',id:'local',name:'This computer'},state:'ready',incompatible:null,
      local:{supported:true,mode:'service',running:'running',prompt_base_url:null},
      candidate:null,access_unsaved:false,locale:{current:'en',persisted:true}});
    if(command==='client.status')return JSON.stringify({stage:'idle',runtime:'unknown',savedAccess:'none',
      route:'unknown',observedAt:null,observation:'known',computers:[],activeComputerRef:null,
      expiresAt:null,comparisonPhrase:null});
    if(command==='host.status'){
      if(api.hold)return new Promise((resolve,reject)=>api.reads.push({resolve,reject}));
      if(api.dispatched && api.after==='throw')throw 'synthetic transport loss';
      return JSON.stringify(api.dispatched?api.snapshot():off);
    }
    if(command==='host.enable'){
      if(api.kind==='denied')throw JSON.stringify({code:'permissionDenied',effect:'none',recovery:'nativePermission'});
      api.terminal=false;
      api.dispatched=true;
      const result={...admission};
      const attempts=api.calls.filter(item=>item.command==='host.enable').length;
      if(attempts>1)result.operationId=attempts.toString(16).padStart(32,'0');
      admission.operationId=result.operationId;
      if(api.kind==='unknown')delete result.operationId;
      return JSON.stringify(result);
    }
    if(command==='app.ownersChanged')return '{}';
    throw JSON.stringify({code:'unsupportedCommand',effect:'none',recovery:'none'});
  },
  on(event,handler){
    if(!listeners.has(event))listeners.set(event,new Set());
    listeners.get(event).add(handler);
    return ()=>listeners.get(event).delete(handler);
  }
});
function Mounted(){
  const ref=useRef(null), [open,setOpen]=useState(true), {setLocale}=useTranslation();
  useEffect(()=>{api.close=()=>setOpen(false);api.open=()=>setOpen(true);api.locale=setLocale;},[setLocale]);
  return <Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[90vh] overflow-auto">
    <ConnectionDialogBody headingRef={ref} onOpenSettings={()=>{}}/>
  </DialogContent></Dialog>;
}
export function EnableFailureCases(){
  return <I18nProvider initialResolution={{locale:params.get('locale')==='zh-CN'?'zh-CN':'en',source:'saved'}}>
    <OwnerGateProvider><Mounted/></OwnerGateProvider>
  </I18nProvider>;
}
`.replace('__TRACE__', JSON.stringify(trace));
await writeFile(join(scratch, 'enable-failure-cases.tsx'), source);
const vite = await startViteHarness({
  importPath: '/.screenshot-harness-tmp/enable-failure-cases.tsx',
  exportName: 'EnableFailureCases', render: '<EnableFailureCases/>',
});

async function browserCase(page, input) {
  const host = page.locator('[data-testid="connection-host"]');
  const title=input.locale==='zh-CN'?'开启失败。':'Turn on failed.';
  async function reset() {
    await page.evaluate(()=>globalThis.enableCase.close());
    await host.waitFor({state:'detached'});
    await page.evaluate(()=>{Object.assign(globalThis.enableCase,{terminal:false,dispatched:false,hold:false,kind:'failed',after:'off',category:'inventory'});globalThis.enableCase.open();});
    await host.locator('[data-command="host.enable"]').waitFor();
  }
  // BF2/BF3/BF4/BF6 execute on the real mounted hook; the three screenshot
  // states are restored afterward. Each click is a distinct deliberate attempt.
  for(const [category,en,zh] of input.reasons) {
    await page.evaluate(category=>{globalThis.enableCase.category=category;},category);
    await host.locator('[data-command="host.enable"]').click();
    await page.evaluate(()=>globalThis.enableCase.emit());
    await host.getByText(input.locale==='zh-CN'?zh:en,{exact:false}).waitFor();
    await host.locator('[data-command="host.enable"]').waitFor();
  }
  for(const first of [0,1]) {
    await reset();
    await page.evaluate(()=>{globalThis.enableCase.hold=true;});
    await host.locator('[data-command="host.enable"]').click();
    await page.waitForFunction(()=>globalThis.enableCase.reads.length===1);
    await page.evaluate(()=>globalThis.enableCase.emit());
    await page.waitForFunction(()=>globalThis.enableCase.reads.length===2);
    await page.evaluate(first=>{globalThis.enableCase.resolveRead(first===0?'ready':'throw',first);
      globalThis.enableCase.resolveRead(first===0?'throw':'ready');globalThis.enableCase.hold=false;},first);
    await host.getByText(title,{exact:false}).waitFor();
    if(await host.locator('[data-command="host.createInvite"]').count())throw Error('Old read authorized invite');
  }
  for(const state of ['off','ready','unavailable','throw']) {
    await reset();
    await page.evaluate(state=>{globalThis.enableCase.after=state;},state);
    await host.locator('[data-command="host.enable"]').click();
    await page.evaluate(()=>globalThis.enableCase.emit());
    await host.getByText(title,{exact:false}).waitFor();
    await page.waitForFunction(()=>globalThis.enableCase.calls.at(-1).terminal);
    if(['unavailable','throw'].includes(state) && await host.locator('[data-command="host.createInvite"]').count())throw Error('Unsafe action after failed observation');
  }
  for(const state of ['off','ready','unavailable']) {
    await reset();
    await page.evaluate(state=>{Object.assign(globalThis.enableCase,{kind:'completed',after:state});},state);
    await host.locator('[data-command="host.enable"]').click();
    await page.evaluate(()=>globalThis.enableCase.emit());
    await page.waitForFunction(()=>globalThis.enableCase.calls.at(-1).command==='host.status');
    if((await host.innerText()).includes(title))throw Error('Completed became preparation failure');
    if((await host.innerText()).includes('confirmed Turn on'))throw Error('Completed invented confirmed effects');
  }
  await reset();
  await page.evaluate(()=>{globalThis.enableCase.kind='denied';});
  await host.locator('[data-command="host.enable"]').click();
  await host.locator('[data-testid="connection-host-outcome"]').waitFor();
  if((await host.innerText()).includes(title))throw Error('Denial became preparation failure');
  await reset();
  await host.locator('[data-command="host.enable"]').waitFor();
  await page.evaluate(({state,kind})=>{Object.assign(globalThis.enableCase,{after:state,kind});}, input);
  const before = await page.evaluate(()=>globalThis.enableCase.calls.length);
  await host.locator('[data-command="host.enable"]').click();
  await page.waitForFunction(()=>globalThis.enableCase.calls.some(item=>item.command==='host.enable'));
  if(input.kind==='failed') {
    await page.evaluate(()=>globalThis.enableCase.emit());
    await host.getByText(input.locale==='zh-CN'?'开启失败。':'Turn on failed.',{exact:false}).waitFor();
    const text=await host.innerText();
    const expected=input.locale==='zh-CN'?'无法验证家庭网络路由。':'The home network routes could not be verified.';
    if(!text.includes(expected)||!text.includes(input.locale==='zh-CN'?'其影响未知。':'Its effect is unknown.'))throw Error('Lost literal failure/effect');
    await page.waitForFunction(()=>globalThis.enableCase.calls.some(item=>item.command==='host.status'&&item.terminal));
    const recovery=host.getByRole('button',{name:input.locale==='zh-CN'?'检查当前状态':'Check current status',exact:true});
    await recovery.focus();
    if(!await recovery.evaluate(el=>el===document.activeElement))throw Error('Recovery not focusable');
    if(input.state==='unavailable' && await host.locator('[data-command="host.createInvite"]').count())throw Error('Unsafe invite while unconfirmed');
    const ledger=await page.evaluate(()=>globalThis.enableCase.calls.length);
    await page.evaluate(locale=>globalThis.enableCase.locale(locale==='en'?'zh-CN':'en'),input.locale);
    await host.getByText(input.locale==='en'?'开启失败。':'Turn on failed.',{exact:false}).waitFor();
    if(await page.evaluate(()=>globalThis.enableCase.calls.length)!==ledger)throw Error('Locale caused native traffic');
    await page.evaluate(locale=>globalThis.enableCase.locale(locale),input.locale);
    await recovery.waitFor();
  } else {
    if((await host.innerText()).includes(input.locale==='zh-CN'?'开启失败。':'Turn on failed.'))throw Error('Unknown became definite failure');
  }
  const mutations=await page.evaluate(before=>globalThis.enableCase.calls.slice(before).filter(x=>x.command==='host.enable').length,before);
  if(mutations!==1)throw Error('Enable replayed');
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Horizontal overflow');
  // No source or host metadata is allowed into the rendered surface.
  const text=await host.innerText();
  for(const canary of ['SYNTHETIC_PRIVATE_CANARY','/synthetic/private-canary','192.0.2.123'])if(text.includes(canary))throw Error('Diagnostic leak');
  await page.evaluate(()=>{globalThis.enableCase.close();});
  await host.waitFor({state:'detached'});
  await page.evaluate(()=>{globalThis.enableCase.terminal=false;globalThis.enableCase.dispatched=false;globalThis.enableCase.open();});
  await host.locator('[data-command="host.enable"]').waitFor();
  if(await host.locator('[data-testid="connection-host-outcome"]').count())throw Error('Failure replayed on reopen');
  // Restore the screenshot state with one NEW explicit attempt.
  await host.locator('[data-command="host.enable"]').click();
  if(input.kind==='failed')await page.evaluate(()=>globalThis.enableCase.emit());
  if(input.kind==='failed')await host.locator('[data-testid="connection-host-outcome"]').waitFor();
  return {before, explicitAttempts:2};
}

const manifest = [];
const proofOnly = process.env.ENABLE_FAILURE_CASE_PROOF === '1';
try {
  for (const locale of proofOnly ? ['en'] : ['en', 'zh-CN']) for (const viewport of proofOnly ? [[390,844]] : [[390,844],[1440,900]]) {
    for (const [state,kind] of proofOnly ? [['off','failed']] : [['off','failed'],['unavailable','failed'],['unavailable','unknown']]) {
      const name = [locale,viewport.join('x'),state,kind].join('-');
      const file = resolve(output, name + '.png');
      await capture({url:vite.url('light')+'&locale='+locale, out:file, viewport, appTheme:false,
        session:'enable-failure', prep:[['run-code','async page => ('+browserCase.toString()+')(page,'+
          JSON.stringify({locale,state,kind,reasons})+')']]});
      const bytes=await readFile(file);
      manifest.push({file,locale,viewport,state,kind,sourceSha,traceSha,
        evidenceKind:'synthetic bridge presentation + composed native trace',
        sha256:createHash('sha256').update(bytes).digest('hex')});
    }
  }
  await writeFile(resolve(output,'enable-failure-screenshots.json'),JSON.stringify(manifest,null,2)+'\n');
} finally {
  await vite.stop();
}
