const f=require('fs'), p=require('path');
const crypto=require('crypto');
const selection=JSON.parse(f.readFileSync('/ordinary-selection.json','utf8'));
const root='/scratch/naming-tests', output='/evidence/fixture-artifacts';
let files=0, bytes=0;
function walk(directory, target, relative=[]) {
  for(const entry of f.readdirSync(directory,{withFileTypes:true})) {
    const source=p.join(directory,entry.name), next=[...relative,entry.name];
    const chosen=next.includes('evidence') || next.includes('receipts') || entry.name==='ownership.json';
    if(entry.isSymbolicLink()) { if(chosen) throw Error('symlink in required evidence:'+source); continue; }
    if(entry.isDirectory()) walk(source,p.join(target,entry.name),next);
    else if(entry.isFile() && chosen) {
      const size=f.statSync(source).size;
      if(size>8*1024**2) throw Error('required evidence exceeds per-file limit:'+source);
      files++;bytes+=size;
      if(files>10000 || bytes>192*1024**2) throw Error('required evidence aggregate limit');
      f.mkdirSync(p.dirname(p.join(target,entry.name)),{recursive:true});
      f.copyFileSync(source,p.join(target,entry.name));
    }
  }
}
if(f.existsSync(root)) {
  for(const entry of f.readdirSync(root,{withFileTypes:true}))
    if(entry.isDirectory()) walk(p.join(root,entry.name),p.join(output,entry.name));
}
f.writeFileSync('/evidence/fixture-export.json',JSON.stringify({files,bytes,workloadExit:Number(process.argv[2])})+'\n');
// Hash the complete retained export, including partial evidence, before host copy.
// The host authenticates this inventory before any destructive storage cleanup.
f.writeFileSync('/evidence/ordinary-selection.json',JSON.stringify(selection)+'\n');
const inventory=[];
function inventoryWalk(directory, relative='') {
  for(const entry of f.readdirSync(directory,{withFileTypes:true})) {
    const name=p.join(relative,entry.name), source=p.join(directory,entry.name);
    if(entry.isSymbolicLink()) throw Error('symlink in export:'+source);
    if(entry.isDirectory()) inventoryWalk(source,name);
    else if(entry.isFile()) {
      const raw=f.readFileSync(source);
      inventory.push({path:name,bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex')});
    } else throw Error('unsupported export entry:'+source);
  }
}
inventoryWalk('/evidence');
f.writeFileSync('/evidence/export-manifest.json',JSON.stringify({source_sha:f.readFileSync('/evidence/source-sentinel','utf8'),files:inventory})+'\n');
if(Number(process.argv[2])===0) {
  for(const name of ['naming.log','naming.xml','work-status.json','boundary-completed.json'])
    if(!f.existsSync('/evidence/'+name) || f.statSync('/evidence/'+name).size===0)
      throw Error('missing required completed evidence:'+name);
  // Successful pytest tmp_path cleanup must never erase the native proof.
  if(files===0) throw Error('missing required native fixture evidence');
  const cases=f.readdirSync(output,{withFileTypes:true}).filter(e=>e.isDirectory()).map(e=>p.join(output,e.name,'evidence'));
  const requireFile=path=>{
    if(!f.existsSync(path) || !f.statSync(path).isFile() || f.statSync(path).size===0)
      throw Error('missing required native evidence:'+path);
    return f.readFileSync(path);
  };
  const json=path=>JSON.parse(requireFile(path));
  const daemons=cases.filter(directory=>f.existsSync(p.join(directory,'daemon-owned.json')));
  // Exact selected case identities, including C1 and the four C2 parameters.
  const expected=selection.cases.filter(row=>row.daemon).map(row=>row.nodeid).sort();
  const actual=daemons.map(directory=>json(p.join(directory,'naming-case.json')).nodeid).sort();
  if(JSON.stringify(actual)!==JSON.stringify(expected)) throw Error('missing/duplicate/foreign daemon case receipt');
  const byCase=new Map(daemons.map(directory=>[json(p.join(directory,'naming-case.json')).nodeid,directory]));
  const prefix='tests/integration/test_identity_names_e2e.py::';
  const fresh=json(p.join(byCase.get(prefix+'test_scenario1_fresh_human_default_naming_I_composition'),'fresh-human-I-public.json'));
  if(fresh.errors.length || !fresh.observations.before || !fresh.observations.after ||
     !fresh.canonical_before || !fresh.canonical_after || !fresh.no_launch_after || !fresh.rename_audit_details)
    throw Error('incomplete C1 public/persisted/readback evidence');
  for(const parameter of ['effort-high','clear-effort','no-options','model-and-effort']) {
    const directory=byCase.get(prefix+'test_scenario8_cli_reasoning_composition['+parameter+']');
    const result=json(p.join(directory,'reasoning-case-'+parameter+'.json'));
    const cli=json(p.join(directory,'reasoning-'+parameter+'.json'));
    if(result.case!==parameter || result.errors.length || !result.seed_definition_hex || !result.after_definition_hex ||
       !result.public_before || !result.public_after || !result.files_before || !result.files_after ||
       !result.beta_identity_before || !result.beta_identity_after || !result.no_launch_after ||
       !result.actual_cli_witness || cli.exit!==0 || cli.first_failure!==null || cli.errors.length || cli.group_absent!==true || cli.elapsed_seconds>15 || cli.captured_bytes>262144)
      throw Error('incomplete C2 full-definition/public/persisted/CLI evidence:'+parameter);
    requireFile(p.join(directory,'reasoning-'+parameter+'.log'));
  }
  for(const directory of daemons) {
    json(p.join(directory,'daemon-owned.json'));
    const closed=json(p.join(directory,'daemon-closure.json'));
    if(closed.stop_exit!==0 || !closed.pid_dead || !closed.listener_closed || closed.remaining_native_pids.length)
      throw Error('incomplete actual daemon closure:'+directory);
  }
  const sql=cases.filter(directory=>f.existsSync(p.join(directory,'sql-observations.jsonl')));
  if(!sql.length) throw Error('missing actual SQL observations');
  for(const directory of sql) requireFile(p.join(directory,'sql-observations.jsonl'));
  const served=cases.filter(directory=>f.existsSync(p.join(directory,'served-openapi.json')));
  if(served.length!==1) throw Error('missing unique served-schema evidence');
  json(p.join(served[0],'served-openapi.json'));
  const witnesses=cases.filter(directory=>f.existsSync(p.join(directory,'witness','identities.jsonl')));
  if(!witnesses.length) throw Error('missing genuine callback witness');
  for(const directory of witnesses) requireFile(p.join(directory,'witness','identities.jsonl'));
  for(const scenario of ['agent','founder','picker']) {
    const found=cases.map(directory=>p.join(directory,'browser-'+scenario)).filter(directory=>f.existsSync(p.join(directory,'result.json')));
    if(found.length!==1) throw Error('missing unique browser proof:'+scenario);
    const directory=found[0], result=json(p.join(directory,'result.json'));
    const closed=json(p.join(directory,'browser-closure.json'));
    if(result.ownedChildrenClosed!==true || closed.remainingPids.length)
      throw Error('incomplete actual browser closure:'+scenario);
    requireFile(p.join(directory,'native-children.jsonl'));
    if(scenario==='picker') {
      const expected=[];
      for(const width of [390,1440]) for(const locale of ['en','zh-CN'])
        for(const theme of ['light','dark']) for(const route of ['agents-maker','settings-organization','threads','tasks'])
          expected.push(`${route}-${width}-${locale}-${theme}.png`);
      if(JSON.stringify([...result.screenshots].sort())!==JSON.stringify(expected.sort()))
        throw Error('incomplete authored route/viewport/locale/theme captures');
      for(const name of expected) {
        const png=requireFile(p.join(directory,name));
        if(png.length>4*1024**2 || !png.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))
          throw Error('invalid required screenshot:'+name);
      }
    }
  }
}
console.log('fixture export complete',files,bytes);
