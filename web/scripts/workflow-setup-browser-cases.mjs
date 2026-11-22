/** Unit (a) cases in the existing ordinary-build CDP/screenshot venue.
 * Synthetic HTTP responses are stimuli for the shipping parser/client/provider.
 * This fixture does not implement a send fence, CAS, authentication or SQLite.
 * Actual persistence/authority is exercised by test-workflow-schema-load.sh.
 */
const copy = structuredClone;
const quote = JSON.stringify;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const OTHER = 'workflow-other';

export function createWorkflowSetupFixture(seed, baseAgent, org) {
  let current, definitionMode, readMode, mutationMode, lookupMode;
  let held = [], heldLookups = [], heldReads = [], originals = new Map();
  const json = (res, status, body) => {
    res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    res.end(JSON.stringify(body));
  };
  const fail = (res, status, code) => json(res, status, { detail: { code } });
  const baseline = () => copy(seed.saved.original);
  const view = (generation, admission = 'Disabled') => {
    const value = baseline(); value.generation = generation; value.snapshot.admission = admission;
    value.id = `browser-settings-${generation}`;
    return value;
  };
  const legacy = () => ({ format: 'workflow-sequential-settings-view@1', id: 'browser-legacy', generation: 1,
    snapshot_digest: 'b'.repeat(64), snapshot: { format: 'workflow-sequential-settings-storage@1',
      definition: copy(seed.legacy_command.definition), bindings: copy(seed.legacy_command.bindings),
      limits: copy(seed.legacy_command.limits), admission: 'Disabled' } });
  const fixture = {
    seed,
    reset({ state = 'Disabled', definitions = 'mixed', read = 'ok' } = {}) {
      held = []; heldLookups = []; heldReads = []; originals = new Map();
      current = { [org]: state === 'none' ? null : state === 'legacy' ? legacy() : view(1, state), [OTHER]: view(1) };
      definitionMode = definitions; readMode = read; mutationMode = 'ok'; lookupMode = 'ok';
    },
    setMutation(mode) { mutationMode = mode; },
    setLookup(mode) { lookupMode = mode; },
    setRead(mode) { readMode = mode; },
    setCurrent(value, slug = org) { current[slug] = copy(value); },
    current: (slug = org) => copy(current[slug]),
    view,
    release() { const values = held.splice(0); values.forEach(f => f()); },
    releaseOne() { held.shift()?.(); },
    releaseLookup() { const values = heldLookups.splice(0); values.forEach(f => f()); },
    releaseReads() { const values = heldReads.splice(0); values.forEach(f => f()); },
    heldCount: () => held.length,
    heldReadCount: () => heldReads.length,
    handle(req, res, path, row) {
      if (path === '/api/v1/orgs') { json(res, 200, { orgs: [org, OTHER].map(slug => ({ slug, root: `/private/${slug}` })), broken: [] }); return true; }
      if (/\/runtime\/workflow-setup\/orgs\//.test(path)) {
        json(res, 200, { format: 'workflow-setup-status@1', org_slug: org, state: 'installed_verified', reason: 'registered',
          attempt_time: null, backup_path: null, writer_blockers: null, integrity_report: null }); return true;
      }
      const match = path.match(/^\/api\/v1\/orgs\/([^/]+)\/(.*)$/);
      if (!match || ![org, OTHER].includes(match[1])) return false;
      const [, slug, leaf] = match;
      if (leaf === 'agents') { json(res, 200, { agents: seed.agents.map(agent => ({ ...baseAgent, ...agent })) }); return true; }
      if (!leaf.startsWith('workflows/sequential/')) return false;
      if (leaf.endsWith('/definitions')) {
        if (definitionMode === 'loading') return 'hang';
        if (definitionMode === 'denied') { fail(res, 403, 'synthetic_denied'); return true; }
        if (definitionMode === 'error') { fail(res, 503, 'synthetic_read_error'); return true; }
        const data = copy(seed.definitions);
        if (definitionMode === 'reverse') data.definitions.reverse();
        if (definitionMode === 'legacy') data.definitions = data.definitions.filter(d => d.pin.version === 2);
        if (definitionMode === 'empty') data.definitions = [];
        if (definitionMode === 'malformed') data.definitions.find(d => d.pin.version === 3).definition_json = '{"format":"unsupported"}';
        json(res, 200, data); return true;
      }
      if (leaf.endsWith('/views/v2/settings')) {
        const value = copy(current[slug]);
        row.readGeneration = value?.generation ?? null;
        row.responseReleased = false;
        const finish = () => {
          row.responseReleased = true;
          return value ? json(res, 200, value) : fail(res, 404, 'sequential_settings_not_found');
        };
        if (readMode === 'hold') heldReads.push(finish);
        else if (readMode === 'denied') fail(res, 403, 'synthetic_denied');
        else if (readMode === 'error') fail(res, 503, 'synthetic_read_error');
        else finish();
        return true;
      }
      if (leaf.endsWith('/operations')) {
        const key = new URL(req.url, 'http://private').searchParams.get('operation_key');
        const original = copy(originals.get(key));
        const mode = lookupMode;
        const finish = () => {
          if (mode === 'absent' || !original) return fail(res, 404, 'sequential_settings_operation_not_found');
          if (mode === 'error') return fail(res, 503, 'synthetic_lookup_error');
          if (mode === 'foreign') original.org_slug = OTHER;
          if (mode === 'key') original.operation_key = 'wrong-key';
          if (mode === 'kind') original.format = original.format.includes('admission') ? 'workflow-sequential-settings-receipt@2' : 'workflow-sequential-admission-receipt@1';
          if (mode === 'pin') original.command.definition.definition_digest = '0'.repeat(64);
          if (mode === 'body') original.command.expected_generation += 1;
          if (mode === 'metadata') original.receipt.generation += 1;
          if (mode === 'limits') original.original.snapshot.limits = { content_returns: 1, max_content_epochs: 2, max_visits: 1, max_events: 4 };
          json(res, 200, original);
        };
        if (mode === 'hold') heldLookups.push(finish); else finish();
        return true;
      }
      if (req.method === 'PUT') {
        let raw = '';
        req.on('data', chunk => { raw += chunk; });
        req.on('end', () => {
          const command = JSON.parse(raw); row.body = command; row.raw = raw;
          const mode = mutationMode;
          const finish = () => {
            if (mode === 'stale' || mode === 'key') return fail(res, 409, mode === 'stale' ? 'sequential_settings_generation_stale' : 'sequential_settings_operation_conflict');
            // Controlled response data only. No validity/CAS/fence decision is
            // made here; commands are asserted independently in the case below.
            const admission = command.format === 'workflow-sequential-admission@1';
            const generic = command.definition.compiler.endsWith('@2');
            const original = copy(current[slug] || baseline());
            original.format = `workflow-sequential-settings-view@${generic ? 2 : 1}`;
            original.generation = command.expected_generation + 1;
            original.id = `browser-settings-${original.generation}`;
            original.snapshot.format = `workflow-sequential-settings-storage@${generic ? 2 : 1}`;
            original.snapshot.definition = copy(command.definition);
            if (admission) original.snapshot.admission = command.admission;
            else { original.snapshot.bindings = copy(command.bindings); original.snapshot.limits = copy(command.limits); }
            const receipt = { format: admission ? 'workflow-sequential-admission-receipt@1' : `workflow-sequential-settings-receipt@${generic ? 2 : 1}`,
              org_slug: slug, operation_key: command.operation_key, command: copy(command),
              receipt: { format: 'workflow-sequential-settings-receipt-storage@1', operation_id: `browser-operation-${original.generation}`,
                settings_id: original.id, generation: original.generation, snapshot_digest: original.snapshot_digest }, original };
            originals.set(command.operation_key, copy(receipt)); current[slug] = copy(original);
            if (mode === 'lost') { res.destroy(); return; }
            json(res, 200, mode === 'malformed' ? { ...receipt, receipt: { ...receipt.receipt, generation: -1 } } : receipt);
          };
          if (mode === 'hold') held.push(finish); else finish();
        });
        return true;
      }
      if (leaf.endsWith('/runs')) { json(res, 200, { format: 'workflow-sequential-runs-view@2', org_slug: slug, storage: 'readable', runs: [] }); return true; }
      return false;
    },
  };
  fixture.reset();
  return fixture;
}

const SELECT = role => `document.getElementById(${quote(`seqwf-role-${role}`)})`;
const BUTTON = label => `[...document.querySelectorAll('button')].find(b => b.textContent.trim() === ${quote(label)})`;
const section = id => `document.querySelector('[aria-labelledby="seqwf-setup-${id}"]')`;

export async function runWorkflowSetupCases(h, { org, fixture: f, selected = 'all' }) {
  const { openPage, closePage, evaluate, waitTrue, clickSrc, capture, check, beginCase, endCase, cdp, ledger, base, tr, seedLocale, crossTabSwitch } = h;
  const want = id => selected === 'all' || selected.split(',').includes(id);
  const puts = from => ledger.slice(from).filter(r => r.method === 'PUT' && r.path.includes('/workflows/sequential/settings'));
  const featureReads = from => ledger.slice(from).filter(r => r.path.includes('/workflows/sequential/'));
  const label = (key, locale = 'en') => tr(locale, `seqwf.setup.${key}`);
  const button = (key, locale = 'en') => BUTTON(label(key, locale));
  const locked = (key, locale = 'en') => `!${button(key, locale)} || ${button(key, locale)}.getAttribute('aria-disabled') === 'true'`;
  const text = value => `document.body.textContent.includes(${quote(value)})`;
  async function open(options = {}, { locale = 'en', theme = 'light', width = 1440 } = {}) {
    f.reset(options);
    const page = await openPage(`${base}/orgs/${org}/workflows/setup`, { width, height: width === 390 ? 844 : 900,
      init: `${seedLocale(locale)}\nlocalStorage.setItem('happyranch.theme', ${quote(theme)});` });
    const hasForm = !['loading','empty','malformed','error','denied'].includes(options.definitions);
    await waitTrue(page, hasForm ? `Boolean(${SELECT('developer')})` : `Boolean(document.querySelector('main'))`, 'setup mounted');
    if (hasForm) await waitTrue(page, `!(${SELECT('developer')}.disabled)`, 'settings and roster settled');
    return page;
  }
  async function pick(page, role, value) {
    await evaluate(page, `(() => { const el = ${SELECT(role)}; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, ${quote(value)}); el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await waitTrue(page, `${SELECT(role)}?.value === ${quote(value)}`, `selected ${role}`);
  }
  async function dirty(page) {
    // Swap two independent worker roles in separate event turns, ending valid.
    await pick(page, 'qa', 's3_review'); await pick(page, 'reviewer', 's3_alt');
  }
  async function fire(page, key, locale = 'en') {
    await clickSrc(page, button(key, locale));
  }
  async function mutation(page, key) {
    const from = ledger.length; await fire(page, key);
    const deadline = Date.now() + 5000;
    while (!puts(from).length && Date.now() < deadline) await pause(20);
    await pause(150); return puts(from);
  }
  async function navigate(page, slug, view = 'setup') {
    await evaluate(page, `(() => { history.pushState({}, '', ${quote(`/orgs/${slug}/workflows/${view}`)}); window.dispatchEvent(new PopStateEvent('popstate')); })()`);
    await waitTrue(page, `location.pathname === ${quote(`/orgs/${slug}/workflows/${view}`)}`, 'route change');
    await pause(250);
  }
  async function shot(page, name, meta = {}) {
    await capture(page, name, { scope: 'ordinary-dist/shipping-provider/synthetic-api', ...meta });
  }

  if (want('A00.2')) {
    for (const definitions of ['mixed','reverse','legacy']) {
      beginCase(`A00.2-${definitions}`, 'real setup parser selects latest release in either order and keeps @1 Save');
      const page = await open({ state: 'none', definitions });
      const released = JSON.parse(f.seed.definitions.definitions.find(d => d.pin.version === (definitions==='legacy'?2:3)).definition_json);
      check('declared responsibilities are rendered',await evaluate(page,quote(released.nodes.flatMap(n=>n.responsibilities))+'.every(s=>document.body.textContent.includes(s))'),true);
      check('declared independence is rendered',await evaluate(page,quote(Object.values(released.roles).flatMap(r=>r.independent_of))+'.every(s=>document.body.textContent.includes(s))'),true);
      for (const [role, agent] of [['developer','s3_author'],['reviewer','s3_review'],['qa','s3_alt'],['final_manager','s3_manager']]) await pick(page, role, agent);
      const rows = await mutation(page, 'save');
      check('one explicit Save', rows.length, 1);
      check('correct ABI route', rows[0]?.path.endsWith(definitions === 'legacy' ? '/settings' : '/settings/v2'), true);
      check('exact released pin', rows[0]?.body.definition, definitions === 'legacy' ? f.seed.legacy_command.definition : f.seed.saved.command.definition);
      check('four actual roles', rows[0]?.body.bindings.map(b => b.role).sort(), ['developer','final_manager','qa','reviewer']);
      check('current CAS starts at zero', rows[0]?.body.expected_generation, 0);
      await closePage(page); endCase();
    }
    for (const definitions of ['malformed','empty']) {
      beginCase(`A00.2-${definitions}`, 'bad/absent definitions have no working mutation');
      const page = await open({ definitions });
      check('visible unavailable text', await waitTrue(page, text(tr('en', definitions === 'empty' ? 'seqwf.definitionsEmpty' : 'seqwf.defUnparsed')), 'visible definition state'), true);
      check('no role editor or Save', await evaluate(page, `!${SELECT('developer')} && !${button('save')}`), true);
      await closePage(page); endCase();
    }
  }
  if (want('W01')) {
    beginCase('W01', 'admission preserves unsaved v3 roles and explicit Save uses new CAS');
    const page = await open(); await dirty(page);
    const before = f.current();
    const allow = await mutation(page, 'allow');
    check('one separate Allow PUT', allow.length, 1);
    check('admission has closed action-only keys', Object.keys(allow[0]?.body || {}).sort(), ['admission','definition','expected_generation','format','operation_key']);
    check('Allow uses saved pin and basis', [allow[0]?.body.definition,allow[0]?.body.expected_generation,allow[0]?.body.admission], [before.snapshot.definition,1,'Allowed']);
    check('admission response leaves draft dirty', await waitTrue(page, `${SELECT('qa')}?.value === 's3_review' && ${text(label('unsaved'))}`, 'dirty retained'), true);
    check('admission receipt rendered',await waitTrue(page,`Boolean(${section('receipt')})`,'admission receipt'),true);
    check('admission receipt never claims Save or unchanged admission',await evaluate(page,`!${section('receipt')}.textContent.includes('Your confirmed save') && !${section('receipt')}.textContent.includes('admission was not changed')`),true);
    check('saved defaults unchanged by Allow stimulus', f.current().snapshot.bindings, before.snapshot.bindings);
    const allowedSave = await mutation(page, 'save');
    check('explicit Save while Allowed sends dirty choices at new CAS', [allowedSave.length,allowedSave[0]?.body.expected_generation,allowedSave[0]?.body.bindings.find(b => b.role === 'qa')?.principal,allowedSave[0]?.body.bindings.find(b => b.role === 'reviewer')?.principal], [1,2,'s3_review','s3_alt']);
    check('Save response preserves Allowed', f.current().snapshot.admission, 'Allowed');
    await pick(page,'qa','s3_alt'); await pick(page,'reviewer','s3_review');
    const disable = await mutation(page, 'disable');
    check('Disable separate at new CAS', [disable.length,disable[0]?.body.expected_generation,disable[0]?.body.admission], [1,3,'Disabled']);
    check('Disable preserves dirty choices',await evaluate(page,`${SELECT('qa')}.value === 's3_alt' && ${text(label('unsaved'))}`),true);
    const save = await mutation(page, 'save');
    check('explicit Save sends intended dirty choices', [save.length,save[0]?.body.expected_generation,save[0]?.body.bindings.find(b => b.role === 'qa')?.principal,save[0]?.body.bindings.find(b => b.role === 'reviewer')?.principal], [1,4,'s3_alt','s3_review']);
    check('Save response preserves Disabled', f.current().snapshot.admission, 'Disabled');
    await closePage(page); endCase();
    for (const state of ['none','legacy']) {
      beginCase(`W01-${state}`, 'unconfigured/wrong-version current settings cannot be allowed');
      const page = await open({ state }); const from = ledger.length;
      await evaluate(page, `${button('allow')}?.click()`);
      check('Allow locked', await evaluate(page, locked('allow')), true);
      check('no mutation', puts(from).length, 0);
      if (state === 'legacy') check('old release distinguished', await evaluate(page, text(tr('en','seqwf.setup.versionMismatch',{ version:2 }))), true);
      await closePage(page); endCase();
    }
  }
  if (want('W02')) {
    for (const first of ['save','allow','disable']) {
      beginCase(`W02-${first}`, 'same-turn shared fence survives unmount and isolates org completion');
      const page = await open({ state: first === 'disable' ? 'Allowed' : 'Disabled' }); await dirty(page); f.setMutation('hold');
      const from = ledger.length;
      await evaluate(page, `(() => { const first=${button(first)}, save=${button('save')}, allow=${button('allow')}, disable=${button('disable')}; first.click(); first.click(); save.click(); allow.click(); disable.click(); first.dispatchEvent(new KeyboardEvent('keydown', { key:'Enter', bubbles:true })); first.dispatchEvent(new KeyboardEvent('keyup', { key:'Enter', bubbles:true })); })()`);
      await waitTrue(page, text(label('sending')), 'sending fence');
      check('pending badge names the original action', await evaluate(page, text(first === 'save' ? label('locked') : tr('en','seqwf.setup.admissionLocked',{action:label(first)}))), true);
      if (first !== 'save') check('pending admission never uses the Save lock', await evaluate(page, `!(${text(label('locked'))})`), true);
      check('same-turn actions send once', puts(from).length, 1);
      const original = copy(puts(from)[0]);
      for (const key of ['save','allow','disable']) check(`${key} locked`, await evaluate(page, locked(key)), true);
      await evaluate(page, `${button('allow')}.focus()`);
      for (const type of ['keyDown','keyUp']) await cdp.send('Input.dispatchKeyEvent', { type, key:'Enter', code:'Enter', windowsVirtualKeyCode:13, nativeVirtualKeyCode:13, ...(type==='keyDown'?{text:'\r'}:{}) },page.sessionId);
      check('trusted keyboard activation also stays fenced',puts(from).length,1);
      await navigate(page, org, 'runs'); await navigate(page, org);
      check('pending original survives pane unmount', await waitTrue(page, text(original.body.operation_key), 'original key'), true);
      await navigate(page, OTHER); await dirty(page);
      await fire(page, 'allow'); await pause(150);
      check('other org has its own pending action', puts(from).filter(r => r.path.includes(`/${OTHER}/`)).length, 1);
      // Release A only while B remains pending; the fixture captures requests in order.
      f.releaseOne(); await pause(250);
      check('A key never appears in B', await evaluate(page, `!(${text(original.body.operation_key)})`), true);
      check('A completion cannot release B fence',await evaluate(page,locked('allow')),true);
      f.release(); await pause(100);
      await navigate(page, org);
      check('A completion returns to its own org', await waitTrue(page, text(tr('en',first !== 'save' ? 'seqwf.setup.admissionConfirmed' : 'seqwf.setup.saved',{generation:2})), 'own receipt'), true);
      check('one mutation in originating org', puts(from).filter(r => r.path.includes(`/${org}/`)).length, 1);
      check('original kind/body/key unchanged', puts(from)[0].body, original.body);
      await closePage(page); endCase();
    }
  }
  if (want('W03')) {
    for (const first of ['allow','save']) for (const refusal of ['stale','key']) {
      beginCase(`W03-${first}-${refusal}`, 'cross-action conflict requires explicit adoption and retains generation floor');
      const page = await open(); await dirty(page); f.setMutation(refusal);
      const newer = f.view(4, 'Allowed'); f.setCurrent(newer);
      const rows = await mutation(page, first);
      check('original refused basis is one', rows[0]?.body.expected_generation, 1);
      check('conflict visible', await waitTrue(page, text(label('conflict')), 'conflict'), true);
      check('conflict recovery names the original operation',await waitTrue(page,text(tr('en',first === 'save' ? 'seqwf.setup.conflictBody' : 'seqwf.setup.admissionConflictBody',{generation:4,action:label(first)})),'operation-specific conflict'),true);
      if (first === 'allow') check('admission adoption asks to retry Allow',await evaluate(page,text(tr('en','seqwf.setup.admissionAdoptHelp',{action:label(first)}))),true);
      const from = ledger.length;
      await evaluate(page, `${button('allow')}.click(); ${button('save')}.click(); ${button('disable')}.click()`);
      check('other action cannot escape conflict', puts(from).length, 0);
      check('draft retained in conflict', await evaluate(page, `${SELECT('qa')}.value`), 's3_review');
      f.setMutation('ok'); await fire(page, 'adopt');
      const next = await mutation(page, first === 'allow' ? 'save' : 'disable');
      check('explicit adoption uses independently read CAS', next[0]?.body.expected_generation, 4);
      // An own receipt (g5) must outrank a later current read returning old g2.
      f.setCurrent(f.view(2));
      await navigate(page, org, 'runs'); await navigate(page, org);
      await waitTrue(page, `Boolean(${SELECT('qa')})`, 'returned setup');
      if (first === 'allow') await dirty(page);
      const floor = await mutation(page, first === 'allow' ? 'disable' : 'allow');
      check('late older current never lowers own commit floor', floor[0]?.body.expected_generation, 5);
      await closePage(page); endCase();
    }
  }
  if (want('W03') || want('W03-inflight')) {
    for (const action of ['save','allow']) {
      beginCase(`W03-inflight-${action}`, 'older current GET stays held across own receipt and cannot lower committed defaults or CAS');
      const page = await open();
      f.setCurrent(f.view(2)); await crossTabSwitch(page,'en');
      check('cached current starts at g2',await waitTrue(page,`${section('current')}.textContent.includes('Generation 2 ·')`,'cached g2'),true);
      await dirty(page);
      const older = f.view(1); f.setCurrent(older);
      f.setRead('hold');
      const from = ledger.length;
      await crossTabSwitch(page,'en'); // Existing foreground refresh starts the older current GET.
      const oldRead = ledger.slice(from).find(r => r.path.endsWith('/views/v2/settings'));
      check('older g1 current GET is already held before mutation',[f.heldReadCount(),oldRead?.readGeneration,oldRead?.responseReleased],[1,1,false]);
      f.setMutation('hold');
      const rows = await mutation(page,action);
      check('own mutation starts from cached g2',rows[0]?.body.expected_generation,2);
      f.releaseOne();
      // Invalidation may cancel/reissue the original GET. Keep that refetch stale
      // too: an up-to-date response must not mask a missing committed floor.
      f.setCurrent(older);
      check('own g3 receipt arrives before releasing older GET',await waitTrue(page,text(tr('en',action === 'save' ? 'seqwf.setup.saved' : 'seqwf.setup.admissionConfirmed',{generation:3})),'own committed receipt'),true);
      check('original older GET still unreleased after receipt',oldRead?.responseReleased,false);
      const deadline = Date.now() + 5000;
      while (f.heldReadCount() < 2 && Date.now() < deadline) await pause(20);
      check('original and invalidation refetch remain stale and held',ledger.slice(from).filter(r => r.path.endsWith('/views/v2/settings')).map(r => [r.readGeneration,r.responseReleased]),[[1,false],[1,false]]);
      f.setRead('ok'); f.releaseReads();
      check('original older response is now released',oldRead?.responseReleased,true);
      check('shipping UI observes stale current separately from own receipt',await waitTrue(page,text(label('currentOlder')),'older current after own receipt'),true);
      await pause(150);
      check('late current cannot replace submitted roles',await evaluate(page,`${SELECT('qa')}.value === 's3_review' && ${SELECT('reviewer')}.value === 's3_alt'`),true);
      // Allow keeps the existing dirty draft; Save marks it clean.
      if (action === 'save') { await pick(page,'qa','s3_alt'); await pick(page,'reviewer','s3_review'); }
      f.setMutation('ok');
      const next = await mutation(page,'save');
      check('next explicit Save never regresses below committed g3',next[0]?.body.expected_generation,3);
      await closePage(page); endCase();
    }
  }
  if (want('W04')) {
    for (const action of ['save','allow','disable']) for (const failure of ['lost','malformed']) {
      beginCase(`W04-${action}-${failure}`, 'uncertain custody resolves only the exact original operation');
      const page = await open({ state: action === 'disable' ? 'Allowed' : 'Disabled' }); await dirty(page); f.setMutation(failure);
      const original = (await mutation(page, action))[0];
      check('unknown outcome visible', await waitTrue(page, `Boolean(document.getElementById('seqwf-setup-uncertain'))`, 'uncertain'), true);
      check('uncertain badge names the original action',await evaluate(page,text(action === 'save' ? label('locked') : tr('en','seqwf.setup.admissionLocked',{action:label(action)}))),true);
      if (action !== 'save') check('uncertain admission never uses the Save lock',await evaluate(page,`!(${text(label('locked'))})`),true);
      await navigate(page, OTHER);
      check('uncertain original never enters another org',await evaluate(page,`!(${text(original.body.operation_key)})`),true);
      await navigate(page,org);
      check('uncertain original survives org round trip',await waitTrue(page,text(original.body.operation_key),'original org custody'),true);
      const from = ledger.length;
      await evaluate(page, `${button('allow')}.click(); ${button('disable')}.click(); ${button('save')}.click()`);
      check('uncertain actions cannot mutate', puts(from).length, 0);
      for (const mode of ['absent','error','foreign','key','kind','pin','body','metadata','limits']) {
        f.setLookup(mode); await fire(page, 'check');
        await waitTrue(page, `Boolean(${button('check')})`, 'lookup finished'); await pause(150);
        check(`${mode} lookup retains original custody`, await evaluate(page, `Boolean(document.getElementById('seqwf-setup-uncertain')) && ${text(original.body.operation_key)}`), true);
        check(`${mode} preserves unsaved selection`, await evaluate(page, `${SELECT('qa')}.value`), 's3_review');
      }
      // Newer current is a distinct input, never a replacement original.
      f.setCurrent(f.view(9,'Disabled'));
      await navigate(page, org, 'runs'); await navigate(page, org);
      check('uncertain body/key survives remount', await waitTrue(page, text(original.body.operation_key), 'restored original'), true);
      f.setLookup('ok'); await fire(page, 'check');
      check('exact original releases custody', await waitTrue(page, `!document.getElementById('seqwf-setup-uncertain')`, 'confirmed original'), true);
      check('only original-kind GETs while uncertain', ledger.slice(from).filter(r => r.method === 'GET' && r.path.endsWith('/operations')).every(r => r.path.endsWith(action !== 'save' ? '/settings/admission/operations' : '/settings/v2/operations')), true);
      check('no automatic mutation/retry', puts(from).length, 0);
      check('original result is separate from newer current', await evaluate(page, text(tr('en','seqwf.setup.currentNewer'))), true);
      if (action !== 'save') check('admission recovery leaves dirty draft', await evaluate(page, text(label('unsaved'))), true);
      await closePage(page); endCase();
    }
    beginCase('W04-late-refusal', 'late original success after access refusal stays refused');
    const page = await open(); f.setMutation('lost'); await mutation(page,'allow');
    await waitTrue(page, `Boolean(document.getElementById('seqwf-setup-uncertain'))`, 'uncertain');
    f.setLookup('hold'); await fire(page,'check');
    f.setRead('denied'); await navigate(page,org,'runs'); await navigate(page,org);
    check('sibling read refusal hides protected form', await waitTrue(page, text(tr('en','seqwf.denied')), 'access refused'), true);
    f.releaseLookup(); await pause(250);
    check('late original cannot expose protected defaults', await evaluate(page, `!${SELECT('qa')}`), true);
    f.setRead('ok'); await navigate(page,org,'runs'); await navigate(page,org);
    check('fresh access still has original custody', await waitTrue(page, `Boolean(document.getElementById('seqwf-setup-uncertain'))`, 'custody after access return'), true);
    await closePage(page); endCase();
  }
  if (want('W05')) {
    for (const locale of ['en','zh-CN']) for (const theme of ['light','dark']) for (const width of [1440,390]) {
      for (const state of ['Disabled','Allowed','none','loading','empty','error','denied','pending','conflict','uncertain','lookup-absent','lookup-error','lookup-confirmed']) {
        beginCase(`W05-${locale}-${theme}-${width}-${state}`, 'localized setup state, control bounds and screenshot');
        const options = ['loading','empty','error','denied'].includes(state) ? { definitions: state } : { state: state === 'none' ? 'none' : state === 'Allowed' ? 'Allowed' : 'Disabled' };
        const page = await open(options, { locale, theme, width });
        if (['pending','conflict','uncertain'].includes(state) || state.startsWith('lookup-')) {
          f.setMutation({pending:'hold',conflict:'stale',uncertain:'lost'}[state] || 'lost');
          if (state === 'conflict') f.setCurrent(f.view(4,'Allowed'));
          await fire(page,'allow',locale);
          await waitTrue(page, text(tr(locale,state === 'pending' ? 'seqwf.setup.sending' : state === 'conflict' ? 'seqwf.setup.conflict' : 'seqwf.setup.uncertain')), 'requested action state');
          if (state.startsWith('lookup-')) {
            f.setLookup(state==='lookup-absent'?'absent':state==='lookup-error'?'error':'ok');
            await fire(page,'check',locale);
          }
        }
        const expected = { loading:'seqwf.loading',empty:'seqwf.definitionsEmpty',error:'seqwf.error',denied:'seqwf.denied',none:'seqwf.setup.notConfigured',Disabled:'seqwf.setup.admissionDisabled',Allowed:'seqwf.setup.admissionAllowed',pending:'seqwf.setup.sending',conflict:'seqwf.setup.conflict',uncertain:'seqwf.setup.uncertain','lookup-absent':'seqwf.setup.lookupAbsent','lookup-error':'seqwf.setup.lookupError','lookup-confirmed':'seqwf.setup.admissionConfirmed' }[state];
        check('owned state text translated', await waitTrue(page,text(tr(locale,expected,{generation:2})), 'localized state'), true);
        if (['pending','uncertain','lookup-absent','lookup-error'].includes(state)) {
          check('localized admission lock names Allow',await evaluate(page,text(tr(locale,'seqwf.setup.admissionLocked',{action:label('allow',locale)}))),true);
          check('localized admission lock never claims Save',await evaluate(page,`!(${text(label('locked',locale))})`),true);
        }
        if (state === 'conflict') {
          check('localized conflict asks to re-read and retry Allow',await evaluate(page,text(tr(locale,'seqwf.setup.admissionConflictBody',{generation:4,action:label('allow',locale)}))),true);
          check('localized adoption retries Allow using current generation',await evaluate(page,text(tr(locale,'seqwf.setup.admissionAdoptHelp',{action:label('allow',locale)}))),true);
          check('localized admission conflict never asks to save again',await evaluate(page,`!document.body.textContent.includes(${quote(locale === 'en' ? 'before saving again' : '再次保存前')})`),true);
        }
        check('locale and theme', await evaluate(page,'[document.documentElement.lang,document.documentElement.dataset.theme]'), [locale,theme]);
        check('no document horizontal overflow', await evaluate(page,'document.documentElement.scrollWidth <= innerWidth + 1'),true);
        const target = state.startsWith('lookup-') ? state==='lookup-confirmed'?'#seqwf-setup-receipt':'#seqwf-setup-uncertain' : ['pending','conflict','uncertain'].includes(state) ? state === 'pending' ? `[role="status"]` : state === 'uncertain' ? '#seqwf-setup-uncertain' : '[role="alert"]' : ['Disabled','Allowed','none'].includes(state) ? '#seqwf-setup-admission' : 'main';
        await evaluate(page, `document.querySelector(${quote(target)})?.scrollIntoView({block:'center'})`);
        if (['Disabled','Allowed','none'].includes(state)) {
          await evaluate(page,`${section('current')}.scrollIntoView({block:'start'})`);
          await shot(page,`setup-current-${locale}-${theme}-${width}-${state}`,{locale,theme,viewport:`${width}x${width===390?844:900}`,state});
          for (const key of ['allow','disable']) {
            const geometry = await evaluate(page, `(() => { const el=${button(key,locale)}; el.scrollIntoView({block:'center'}); const r=el.getBoundingClientRect(); el.focus(); const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2); return { bounds:r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1, focus:el.disabled||document.activeElement===el, hit:hit===el||el.contains(hit) }; })()`);
            check(`${key} reachable and focusable`,geometry,{bounds:true,focus:true,hit:true});
            if (await evaluate(page, locked(key, locale))) {
              const from = ledger.length;
              await fire(page, key, locale);
              for (const type of ['keyDown','keyUp']) await cdp.send('Input.dispatchKeyEvent', { type, key:'Enter', code:'Enter', windowsVirtualKeyCode:13, nativeVirtualKeyCode:13, ...(type==='keyDown'?{text:'\r'}:{}) },page.sessionId);
              await evaluate(page, `${button(key,locale)}.click()`);
              await pause(150);
              check(`${key} blocked pointer/keyboard/programmatic activation sends nothing`, puts(from).length, 0);
            }
          }
        }
        await shot(page,`setup-${locale}-${theme}-${width}-${state}`,{locale,theme,viewport:`${width}x${width===390?844:900}`,state});
        f.release(); await closePage(page); endCase();
      }
    }
  }
  if (want('W05') || want('W05-switch')) {
    for (const theme of ['light','dark']) for (const width of [1440,390]) {
    beginCase(`W05-switch-${theme}-${width}`, 'locale preserves dirty focus and uncertain original without feature HTTP');
    const page = await open({}, {theme,width}); await dirty(page); f.setMutation('lost');
    const original=(await mutation(page,'allow'))[0];
    await waitTrue(page,`Boolean(document.getElementById('seqwf-setup-uncertain'))`,'uncertain');
    await evaluate(page,`(() => { window.__workflowControl=${SELECT('qa')}; ${button('check')}.focus(); window.__workflowFocus=document.activeElement; window.__workflowVisibility=[]; document.addEventListener('visibilitychange',()=>window.__workflowVisibility.push(document.visibilityState)); return true; })()`);
    // Control: unchanged locale with foreground tab activation causes the existing
    // window-focus refresh. Keep it separate from the real background storage event.
    const controlFrom=ledger.length;
    await crossTabSwitch(page,'en');
    check('same-locale foreground control refreshes only current projections',featureReads(controlFrom).map(r=>[r.method,r.path]).sort(),[
      ['GET',`/api/v1/orgs/${org}/workflows/sequential/views/v2/definitions`],
      ['GET',`/api/v1/orgs/${org}/workflows/sequential/views/v2/settings`],
    ]);
    await evaluate(page,`(() => { window.__workflowVisibility=[]; return true; })()`);
    for (const locale of ['zh-CN','en']) {
      const from=ledger.length; await crossTabSwitch(page,locale,{background:true});
      check(`${locale} isolates locale from window-focus refresh`,await evaluate(page,`[document.visibilityState,window.__workflowVisibility]`),['visible',[]]);
      check(`${locale} creates zero feature reads/mutations`,featureReads(from).length,0);
      check(`${locale} retains control/draft/focus`,await evaluate(page,`${SELECT('qa')}===window.__workflowControl && ${SELECT('qa')}.value==='s3_review' && document.activeElement===window.__workflowFocus`),true);
      check(`${locale} retains original operation`,await evaluate(page,text(original.body.operation_key)),true);
    }
    await closePage(page); endCase();
    }
  }
  beginCase('P01-browser-boundary','setup never sends a Start or domain registration mutation');
  check('no workflow Start/Draft registration request',ledger.filter(r => r.method !== 'GET' && /\/workflows\//.test(r.path) && !/\/settings(?:\/v2|\/admission)?$/.test(r.path)),[]);
  check('real setup GET and mutation paths exercised',ledger.some(r=>r.path.endsWith('/views/v2/definitions'))&&ledger.some(r=>r.method==='PUT'),true);
  endCase();
}
