/** TASK-5593: real ThreadsPage reply-delivery evidence and adversarial probes. */
import { spawn } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createServer, defaultApiRoutes, findDist, WEB_ROOT } from './harness.mjs';

const OUT = join(WEB_ROOT, 'scripts', 'screenshot-harness', 'out', 'task-5593');
const SLUG = 'demo'; const THREAD = 'THR-5593';
const ROUTE = `/orgs/${SLUG}/threads/${THREAD}`;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const baseThread = {
  thread_id: THREAD, subject: 'Reply delivery production seam', status: 'open', started_at: '2026-08-24T13:00:00Z',
  archived_at: null, forwarded_from_id: null, forwarded_from_kind: null, turn_cap: 500, turns_used: 12, summary: null,
  transcript_path: null, composed_from_dream_id: null, last_speaker: 'founder',
  participants: ['founder', 'frontend_engineer_primary', 'frontend_engineer_secondary', 'qa_engineer'],
  messages: [{ seq: 9, speaker: 'founder', kind: 'message', body_markdown: 'Please verify delivery.', decline_reason: null,
    system_payload: null, created_at: '2026-08-24T13:00:00Z', attachments: [], responder_status: [] }],
};
const entry = (agent_name, state, from_seq, through_seq, count, extra = {}) => ({ agent_name, state, from_seq, through_seq,
  coalesced_message_count: count, started_at: state === 'running' ? '2026-08-24T13:00:00Z' : null,
  updated_at: '2026-08-24T13:01:00Z', last_terminal_reason: null, ...extra });
const populated = [entry('frontend_engineer_primary','running',9,9,1), entry('qa_engineer','queued',8,9,2),
  entry('support_lead','retry_required',5,7,3,{ last_terminal_reason:'timeout' })];
const concurrent = [entry('frontend_engineer_primary','running',8,10,3),
  entry('frontend_engineer_secondary','running',11,11,1,{ started_at:'2026-08-24T13:00:20Z' }),
  entry('qa_engineer','queued',12,14,3), entry('support_engineer','queued',15,15,1)];

function apiFor({ replyDelivery, detail = 'ok' }) {
  const json = { ...baseThread, reply_delivery: replyDelivery };
  const path = `/api/v1/orgs/${SLUG}/threads/${THREAD}`;
  const detailRoute = detail === 'loading'
    ? { path, handler: () => {} }
    : detail === 'error' ? { path, handler: (_req,res) => { res.writeHead(500,{'Content-Type':'application/json'}); res.end('{"detail":"boom"}'); } }
      : { path, json };
  return [...defaultApiRoutes({ orgs:[{ slug:SLUG,root:'/tmp/demo' }] }),
    { path:`/api/v1/orgs/${SLUG}/agents`,json:{ agents:[] } }, { path:`/api/v1/orgs/${SLUG}/threads`,json:{ threads:[baseThread] } }, detailRoute,
    { path:`${path}/messages`,json:{ messages:baseThread.messages } }, { path:`${path}/tasks`,json:{ tasks:[] } },
    { path:`/api/v1/orgs/${SLUG}/tokens`,json:{ rollup:[] } }];
}
function pw(session,args) { return new Promise((resolve,reject) => { const proc=spawn('playwright-cli',[`-s=${session}`,...args],{stdio:['ignore','pipe','pipe']});
  let stdout=''; let stderr=''; proc.stdout.on('data',(c)=>{stdout+=c}); proc.stderr.on('data',(c)=>{stderr+=c});
  proc.on('exit',(code)=>code===0?resolve(stdout):reject(new Error(`playwright-cli ${args[0]} failed (${code}): ${stderr}`))); proc.on('error',reject); }); }
function parseEval(stdout) { const marker='### Result'; let raw=stdout.slice(stdout.lastIndexOf(marker)+marker.length).trim();
  const next=raw.indexOf('\n###'); if(next!==-1)raw=raw.slice(0,next).trim(); const once=JSON.parse(raw); return typeof once==='string'?JSON.parse(once):once; }

const diagnosticExpression = (probe='default') => `(() => {
  const section=document.querySelector('[aria-label="Reply delivery"]'); const aside=document.querySelector('aside[aria-label="Thread properties"]');
  if(section&&'${probe}'==='displacement')section.style.transform='translateX(320px)';
  if(section&&aside&&'${probe}'==='ancestor-clip'){section.style.width='420px';aside.style.overflow='hidden'}
  const rect=(el)=>{const r=el.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
  const controls=[...(section?.querySelectorAll('button,a,input,select,textarea,summary,[tabindex]')??[])];
  const controlResults=controls.map((el,index)=>{const r=el.getBoundingClientRect();const ancestors=[];let parent=el.parentElement;
    while(parent){const pr=parent.getBoundingClientRect();const style=getComputedStyle(parent);const overflow=[style.overflow,style.overflowX,style.overflowY];
      if(overflow.some((v)=>['hidden','scroll','auto','clip'].includes(v)))ancestors.push({tag:parent.tagName.toLowerCase(),ariaLabel:parent.getAttribute('aria-label'),overflow,rect:rect(parent),clips:r.left<pr.left||r.right>pr.right||r.top<pr.top||r.bottom>pr.bottom});parent=parent.parentElement}
    el.focus();return{index,tag:el.tagName.toLowerCase(),role:el.getAttribute('role')||(el.tagName==='SUMMARY'?'button':el.tagName.toLowerCase()),accessibleName:el.getAttribute('aria-label')||el.textContent.trim(),expanded:el.parentElement?.tagName==='DETAILS'?el.parentElement.open:el.getAttribute('aria-expanded'),focused:document.activeElement===el,rect:rect(el),viewport:{width:innerWidth,height:innerHeight},inViewport:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,clippingAncestors:ancestors}});
  const identities=section?[...section.querySelectorAll('li')].map((li)=>li.innerText.trim().split('\\n')):[];
  const sectionRect=section?rect(section):null;const asideRect=aside?rect(aside):null;
  return JSON.stringify({probe:'${probe}',sectionPresent:!!section,asidePresent:!!aside,asideRect,expectedRailWidth:244,railWidthPass:!asideRect||Math.abs(asideRect.width-244)<1,sectionRect,
    sectionInViewport:!sectionRect||(sectionRect.x>=0&&sectionRect.right<=innerWidth&&sectionRect.y>=0&&sectionRect.bottom<=innerHeight),
    sectionClipped:!!(sectionRect&&asideRect&&(sectionRect.x<asideRect.x||sectionRect.right>asideRect.right||sectionRect.y<asideRect.y||sectionRect.bottom>asideRect.bottom)),controls:controlResults,identities,bodyText:document.body.innerText});
})()`;
function assertState(result,testCase){if((testCase.expectAside??true)!==result.asidePresent)throw new Error(`${testCase.name}: production aside presence ${result.asidePresent}`);
  if(result.asidePresent&&!result.railWidthPass)throw new Error(`${testCase.name}: rail ${result.asideRect.width}px, expected 244px`);
  if(testCase.expectSection!==result.sectionPresent)throw new Error(`${testCase.name}: section presence ${result.sectionPresent}`);
  if(testCase.expectText&&!result.bodyText.includes(testCase.expectText))throw new Error(`${testCase.name}: missing ${testCase.expectText}`);
  if(result.sectionPresent&&(!result.sectionInViewport||result.sectionClipped))throw new Error(`${testCase.name}: section clipped/outside viewport`);
  for(const control of result.controls)if(!control.accessibleName||!control.focused||!control.inViewport||control.clippingAncestors.some((a)=>a.clips))throw new Error(`${testCase.name}: control failed ${JSON.stringify(control)}`);
  for(const expected of testCase.identities??[])if(!result.identities.some((parts)=>parts.join(' ').includes(expected)))throw new Error(`${testCase.name}: missing ${expected}`);}

const cases=[
  {name:'loading-production',viewport:[1440,720],detail:'loading',expectAside:false,expectSection:false,expectText:'Loading messages…'},
  {name:'empty-production',viewport:[1440,720],expectSection:false,expectText:'Reply delivery production seam'},
  {name:'error-production',viewport:[1440,720],detail:'error',expectAside:false,expectSection:false,expectText:'Failed to load thread.',errorControl:true},
  {name:'populated-production-closed',viewport:[1440,720],replyDelivery:populated,expectSection:true,identities:['frontend_engineer_primary replying','support_lead retry required','messages 5–7']},
  {name:'multi-agent-narrow-closed',viewport:[1048,720],replyDelivery:concurrent,expectSection:true,identities:['frontend_engineer_primary replying','frontend_engineer_secondary replying','messages 8–10']},
  {name:'multi-agent-wide-open',viewport:[1910,720],replyDelivery:concurrent,open:true,expectSection:true,identities:['frontend_engineer_primary replying','frontend_engineer_secondary replying','messages 8–10','qa_engineer 3 messages coalesced','support_engineer 1 message coalesced']},
];
await rm(OUT,{recursive:true,force:true}); await mkdir(OUT,{recursive:true}); const results=[];
for(const testCase of cases){const server=await createServer({root:findDist(),api:apiFor(testCase)});try{for(const theme of ['light','dark']){
  const session=`task5593-${testCase.name}-${theme}`;await pw(session,['open']);try{await pw(session,['resize',String(testCase.viewport[0]),String(testCase.viewport[1])]);
    await pw(session,['goto',`${server.url}${ROUTE}`]);await pw(session,['localstorage-set','happyranch.theme',theme]);await pw(session,['reload']);await sleep(testCase.detail==='error'?1400:700);
    const queued=testCase.replyDelivery?.filter((item)=>item.state==='queued').length??0;if(queued&&testCase.open){await pw(session,['click',`getByText('${queued} queued ${queued===1?'delivery':'deliveries'}')`]);await pw(session,['press','Tab']);await pw(session,['press','Shift+Tab']);}
    const result=parseEval(await pw(session,['eval',diagnosticExpression()]));if(testCase.errorControl){const retry=parseEval(await pw(session,['eval',`(()=>{const el=[...document.querySelectorAll('button')].find((n)=>n.textContent.trim()==='Retry');if(!el)return JSON.stringify({present:false});el.focus();const r=el.getBoundingClientRect();return JSON.stringify({present:true,accessibleName:el.textContent.trim(),focused:document.activeElement===el,inViewport:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,rect:{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}})})()`]));result.pageErrorControl=retry;if(!retry.present||!retry.focused||!retry.inViewport||retry.accessibleName!=='Retry')throw new Error('real Retry control failed')}
    assertState(result,testCase);results.push({case:testCase.name,theme,...result});await pw(session,['screenshot',`--filename=${join(OUT,`${testCase.name}-${theme}.png`)}`]);
  }finally{await pw(session,['close']).catch(()=>{})}}}finally{server.server.closeAllConnections();await server.close()}}

const probeServer=await createServer({root:findDist(),api:apiFor({replyDelivery:populated})});try{for(const probe of ['displacement','ancestor-clip']){
  const session=`task5593-red-${probe}`;await pw(session,['open']);try{await pw(session,['resize','1440','720']);await pw(session,['goto',`${probeServer.url}${ROUTE}`]);await sleep(700);
    const result=parseEval(await pw(session,['eval',diagnosticExpression(probe)]));let detected=false;try{assertState(result,{name:probe,expectSection:true})}catch(error){detected=true;result.expectedFailure=error.message}
    if(!detected)throw new Error(`${probe}: red probe was not detected`);await writeFile(join(OUT,`diagnostics-red-${probe}.json`),`${JSON.stringify(result,null,2)}\n`);
  }finally{await pw(session,['close']).catch(()=>{})}}}finally{await probeServer.close()}
await writeFile(join(OUT,'diagnostics-default.json'),`${JSON.stringify(results,null,2)}\n`);
console.log(JSON.stringify({status:'PASS',output:OUT,captures:results.length,redProbes:['displacement','ancestor-clip']}));
