/** THR-390 PR10 W01-W07. Existing W4a built-SPA/CDP venue, synthetic API only.
 * --slice task-attr-definitions [--tad-case W01,W02,W03,W04,W05,W06,W07]
 * Desktop entry uses the existing presentation-only facade on the HTTP origin;
 * actual native admission/close/Quit schema is covered by native-owners.test.mjs.
 * Never infer a server commit from this fixture or a list match.
 */
const allowed = { can_create: true, can_edit_metadata: true, can_edit_structure: true, can_archive: true, refusal_reason: null };
const definition = (id, key, name, used = false) => ({ definition_id: id, key, name, type: 'boolean', constraints: {}, description: null,
  version: 1, created_at: '2026-11-20T00:00:00Z', recorded_at: '2026-11-20T00:00:00Z', first_used_at: used ? '2026-11-21T00:00:00Z' : null,
  archived_at: null, archived: false });
const read = (d) => ({ definition: d, editability: { ...allowed, can_edit_structure: d.first_used_at === null } });
export function createTaskAttributeDefinitionFixture(org) {
  const path = `/api/v1/orgs/${org}/task-attributes/definitions`;
  const state = { rows: [], posts: [], patches: [], mode: 'success', listFailure: false, delayed: null, holdRead: [] };
  const send = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)); };
  const reset = (mode = 'success', empty = false) => {
    state.rows = empty ? [] : [definition('unused', 'synthetic_unused', 'Synthetic unused'), definition('used', 'synthetic_used', 'Synthetic used', true), definition('long', 'a'.repeat(64), 'Long synthetic name '.repeat(4))];
    state.posts = []; state.patches = []; state.mode = mode; state.listFailure = false; state.delayed = null;
  };
  const commit = (body) => { const d = { ...definition(`created-${state.rows.length}`, 'server_chosen_2', body.name.trim()), ...body }; delete d.expected_version; state.rows.push(d); return d; };
  reset();
  function handle(req, res, p, _url, row) {
    if (p === path && req.method === 'GET') {
      if (state.mode === 'listError') { send(res, 503, { detail: { code: 'attribute_storage_unavailable' } }); return true; }
      if (state.mode === 'listLoading') { state.holdRead.push(() => send(res, 200, { definitions: state.rows, editability: allowed })); return true; }
      if (state.listFailure) { send(res, 422, { detail: { code: 'synthetic_check_failed' } }); return true; }
      send(res, 200, { definitions: state.rows, editability: state.mode === 'readonly' ? { ...allowed, can_create: false, can_edit_metadata: false, can_edit_structure: false, can_archive: false, refusal_reason: { code: 'human_only' } } : allowed });
      return true;
    }
    if (p === path && req.method === 'POST') {
      let raw = '';
      req.on('data', (part) => { raw += part; });
      req.on('end', () => {
        const body = JSON.parse(raw); row.body = body; state.posts.push(body);
        if (state.mode === 'exhausted') { send(res, 422, { detail: { code: 'attribute_validation_failed', errors: { name: ['key_generation_exhausted'] } } }); return; }
        if (state.mode === 'denied') { send(res, 403, { detail: { code: 'human_only' } }); return; }
        if (state.mode === 'pending') { state.delayed = () => send(res, 201, read(commit(body))); return; }
        if (state.mode === 'unknownShape') { send(res, 201, {}); return; }
        if (state.mode.startsWith('unknown')) {
          if (['unknownCommit', 'unknownMetadata', 'unknown502', 'unknown502Gate'].includes(state.mode)) {
            const d = commit(body);
            if (state.mode === 'unknownMetadata') d.name = 'Changed by another tab';
          }
          if (state.mode === 'unknownDelayed') state.delayed = () => commit(body);
          // Ordinary JSON error after forwarding/commit, as emitted by the Linux bridge.
          if (state.mode === 'unknown502' || state.mode === 'unknown502Gate') { send(res, 502, { detail: { code: 'incomplete_body' } }); return; }
          // Real HTTP success headers with an unreadable/truncated JSON body.
          // No automatic resend or list-derived attribution is authorized.
          res.writeHead(201, { 'content-type': 'application/json' }); res.end('{'); return;
        }
        send(res, 201, read(commit(body)));
      });
      return true;
    }
    const id = p.startsWith(`${path}/`) ? p.slice(path.length + 1) : null;
    const d = state.rows.find((item) => item.definition_id === id);
    if (d && req.method === 'GET') { send(res, 200, read(d)); return true; }
    if (d && req.method === 'PATCH') {
      let raw = '';
      req.on('data', (part) => { raw += part; });
      req.on('end', () => {
        const body = JSON.parse(raw); row.body = body; state.patches.push(body);
        if (state.mode === 'frozen' || state.mode === 'archived') {
          const current = structuredClone(d);
          if (state.mode === 'frozen') current.first_used_at = '2026-11-22T00:00:00Z';
          if (state.mode === 'archived') { current.archived = true; current.archived_at = '2026-11-22T00:00:00Z'; }
          const result = read(current);
          if (current.archived) result.editability = { ...allowed, can_edit_metadata: false, can_edit_structure: false, can_archive: false };
          send(res, 409, { detail: { code: state.mode === 'frozen' ? 'definition_structure_frozen' : 'definition_archived', current: result } }); return;
        }
        Object.assign(d, body, { version: d.version + 1 }); delete d.expected_version; send(res, 200, read(d));
      });
      return true;
    }
    return false;
  }
  return { state, reset, handle };
}

export async function runTaskAttributeDefinitionCases(h, { org, only = '', context = '', outcome = '' }) {
  const { openPage, closePage, evaluate, waitTrue, clickSrc, cdp, capture, check, beginCase, endCase, fixture, base, tr, seedLocale, crossTabSwitch, ledger } = h;
  const validCases = ['W01', 'W02', 'W03', 'W04', 'W05', 'W06', 'W07'];
  const selected = new Set(only ? only.split(',') : validCases);
  if ([...selected].some((id) => !validCases.includes(id))) throw new Error('unknown --tad-case (W08 has no Settings prototype renderer)');
  if (context && !/^(en|zh-CN)-(390|1440)-(web|desktop)$/.test(context)) throw new Error('unknown --tad-context');
  if (outcome && !['unknownCommit', 'unknownNoCommit', 'unknownOther', 'unknownMetadata', 'unknownDelayed', 'unknownShape', 'unknown502', 'unknown502Gate'].includes(outcome)) throw new Error('unknown --tad-outcome');
  const editor = `document.querySelector('[data-testid="ta-editor-body"]')?.closest('[role="dialog"]')`;
  const button = (key, locale, scope = editor) => `[...(${scope})?.querySelectorAll('button') ?? []].find(b => b.textContent.trim() === ${JSON.stringify(tr(locale, key))})`;
  const text = (key, locale) => `document.body.textContent.includes(${JSON.stringify(tr(locale, key))})`;
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  async function type(page, selector, value) {
    await evaluate(page, `(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({block:'center'}); e.focus(); e.select(); return true; })()`);
    await cdp.send('Input.insertText', { text: value }, page.sessionId);
  }
  async function enter(page) {
    for (const type of ['keyDown', 'keyUp']) await cdp.send('Input.dispatchKeyEvent', { type, key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, ...(type === 'keyDown' ? { text: '\r' } : {}) }, page.sessionId);
  }
  async function open(locale, width, desktop, mode = 'success', empty = false) {
    fixture.reset(mode, empty);
    const init = seedLocale(locale) + (desktop ? `\nwindow.happyranchConnection = {call: async () => '{}', on: () => () => {}};` : '');
    const page = await openPage(`${base}/orgs/${org}/settings/task-attributes`, { init, width, height: width === 390 ? 844 : 900 });
    const special = { listLoading: 'settings.taskAttributes.list.loadingSr', listError: 'settings.taskAttributes.list.loadError', readonly: 'settings.taskAttributes.list.readonly' }[mode];
    check('Settings entry rendered', await waitTrue(page, special ? text(special, locale) : `!!(${button('settings.taskAttributes.list.create', locale, 'document')})`, 'Settings list'), true);
    if (desktop) check('desktop shared Settings pane', await evaluate(page, `!!document.querySelector('[data-testid="desktop-settings-pane"]')`), true);
    return page;
  }
  async function createDraft(page, locale, name = 'Synthetic new') {
    await clickSrc(page, button('settings.taskAttributes.list.create', locale, 'document'));
    check('create editor opens', await waitTrue(page, `!!document.querySelector('#ta-name')`, 'name field'), true);
    check('no stable-key input', await evaluate(page, `document.querySelectorAll('#ta-key, #ta-locked-key-label').length`), 0);
    await type(page, '#ta-name', name);
    await clickSrc(page, `document.querySelector('#ta-type-boolean')`);
  }
  async function save(page, locale) { await clickSrc(page, button('settings.taskAttributes.editor.saveCreate', locale)); }
  async function geometry(page, id, locale, width) {
    check('no horizontal document overflow', await evaluate(page, 'document.documentElement.scrollWidth <= innerWidth'), true);
    check('editor stays within viewport', await evaluate(page, `(() => { const e = ${editor}; const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1 && r.top >= 0 && r.bottom <= innerHeight + 1; })()`), true);
    await capture(page, id, { locale, width, height: width === 390 ? 844 : 900 });
  }
  // Every changed owned state is rendered in both locales and both sizes;
  // each entry exercises the same shipped component, with its real owner scope.
  for (const locale of ['en', 'zh-CN']) for (const width of [390, 1440]) for (const desktop of [false, true]) {
    const tag = `${locale}-${width}-${desktop ? 'desktop' : 'web'}`;
    if (context && context !== tag) continue;
    if (selected.has('W01') || selected.has('W06')) {
      beginCase(`W01-W06-${tag}`, 'keyless create, authoritative key, locale/layout/keyboard');
      const page = await open(locale, width, desktop, 'success', true);
      await createDraft(page, locale, 'Synthetic new 🧭');
      await geometry(page, `create-${tag}`, locale, width);
      await save(page, locale);
      check('create closes on own success', await waitTrue(page, `!document.querySelector('#ta-name')`, 'saved close'), true);
      check('one POST', fixture.state.posts.length, 1);
      check('POST key omitted', Object.hasOwn(fixture.state.posts[0] ?? {}, 'key'), false);
      check('exact requested body', fixture.state.posts[0], { type: 'boolean', name: 'Synthetic new 🧭', expected_version: 0 });
      check('returned key visible', await waitTrue(page, `document.body.textContent.includes('server_chosen_2')`, 'returned key'), true);
      await closePage(page); endCase();
    }
    if (selected.has('W02')) for (const [id, key] of [['unused', 'synthetic_unused'], ['used', 'synthetic_used'], ['long', 'a'.repeat(64)]]) {
      beginCase(`W02-${id}-${tag}`, 'edit key is read-only, name PATCH never includes key');
      const page = await open(locale, width, desktop);
      const row = `document.querySelector('[data-testid="ta-row-${key}"]')`;
      await waitTrue(page, `!!(${button('settings.taskAttributes.list.edit', locale, row)}) && !(${button('settings.taskAttributes.list.edit', locale, row)}).disabled`, 'row fresh read');
      await clickSrc(page, button('settings.taskAttributes.list.edit', locale, row));
      await waitTrue(page, `!!document.querySelector('#ta-name')`, 'edit name');
      check('one read-only key row, no editable key', await evaluate(page, `[document.querySelectorAll('#ta-locked-key-label').length, document.querySelectorAll('#ta-key').length]`), [1, 0]);
      check('stored key shown', await evaluate(page, `document.querySelector('[aria-labelledby="ta-locked-key-label"]').textContent.includes(${JSON.stringify(key)})`), true);
      check('unused structure remains editable', await evaluate(page, `!!document.querySelector('#ta-type-boolean')`), id !== 'used');
      await type(page, '#ta-name', 'Renamed display name');
      await geometry(page, `edit-${id}-${tag}`, locale, width);
      await clickSrc(page, button('settings.taskAttributes.editor.save', locale));
      check('edit closes on own success', await waitTrue(page, `!document.querySelector('#ta-name')`, 'edit saved'), true);
      check('PATCH excludes key', fixture.state.patches[0], { expected_version: 1, name: 'Renamed display name' });
      await closePage(page); endCase();
    }
    if (selected.has('W03')) for (const mode of ['exhausted', 'denied']) {
      beginCase(`W03-${mode}-${tag}`, 'visible translated refusal preserves draft');
      const page = await open(locale, width, desktop, mode);
      await createDraft(page, locale);
      await save(page, locale);
      const key = mode === 'exhausted' ? 'settings.taskAttributes.editor.err.keyExhausted' : 'settings.taskAttributes.editor.permRevoked';
      // permRevoked has a named org placeholder.
      const expected = tr(locale, key, { org });
      check('localized refusal visible', await waitTrue(page, `document.body.textContent.includes(${JSON.stringify(expected)})`, 'refusal'), true);
      check('draft kept', await evaluate(page, `document.querySelector('#ta-name').value`), 'Synthetic new');
      if (mode === 'exhausted') {
        check('summary targets visible name', await evaluate(page, `!!(${editor}).querySelector('a[href="#ta-name"]')`), true);
        // Keyboard activation hits the wrapped inline link itself; the
        // bounding-box centre can fall between its text fragments.
        await evaluate(page, `(${editor}).querySelector('a[href="#ta-name"]').focus()`);
        await enter(page);
        check('name focus', await waitTrue(page, `document.activeElement.id === 'ta-name'`, 'name focus'), true);
      }
      await geometry(page, `error-${mode}-${tag}`, locale, width);
      await closePage(page); endCase();
    }
    if (selected.has('W03')) for (const mode of ['frozen', 'archived']) {
      beginCase(`W03-${mode}-${tag}`, 'structural freeze/archive conflict retains name draft and never reports Saved');
      const page = await open(locale, width, desktop, mode);
      const row = `document.querySelector('[data-testid="ta-row-synthetic_unused"]')`;
      await waitTrue(page, `!!(${button('settings.taskAttributes.list.edit', locale, row)}) && !(${button('settings.taskAttributes.list.edit', locale, row)}).disabled`, 'fresh row');
      await clickSrc(page, button('settings.taskAttributes.list.edit', locale, row));
      await waitTrue(page, `!!document.querySelector('#ta-name')`, 'edit');
      await type(page, '#ta-name', 'Retained conflict draft');
      await clickSrc(page, button('settings.taskAttributes.editor.save', locale));
      check('conflict refusal visible', await waitTrue(page, text(mode === 'frozen' ? 'settings.taskAttributes.editor.frozenBody' : 'settings.taskAttributes.editor.archivedRefused', locale), '409 refusal'), true);
      check('conflict draft retained', await evaluate(page, `document.querySelector('#ta-name').value`), 'Retained conflict draft');
      check('one PATCH, no key', fixture.state.patches, [{ expected_version: 1, name: 'Retained conflict draft' }]);
      await geometry(page, `conflict-${mode}-${tag}`, locale, width);
      await closePage(page); endCase();
    }
    if (selected.has('W03')) for (const mode of ['listLoading', 'listError', 'readonly']) {
      beginCase(`W03-${mode}-${tag}`, 'loading, read error and read-only list cannot mutate');
      const page = await open(locale, width, desktop, mode);
      check('no create entry', await evaluate(page, `!!(${button('settings.taskAttributes.list.create', locale, 'document')})`), false);
      check('zero definition writes', [fixture.state.posts.length, fixture.state.patches.length], [0, 0]);
      await capture(page, `list-${mode}-${tag}`, { locale, width, desktop });
      for (const release of fixture.state.holdRead.splice(0)) release();
      await closePage(page); endCase();
    }
    if (selected.has('W04') || selected.has('W07')) for (const mode of ['unknownCommit', 'unknownNoCommit', 'unknownOther', 'unknownMetadata', 'unknownDelayed', 'unknownShape', 'unknown502', 'unknown502Gate'].filter((mode) => !outcome || mode === outcome)) {
      beginCase(`W04-W07-${mode}-${tag}`, 'list cannot attribute unknown create; acknowledgement from dialog/shared gate');
      const page = await open(locale, width, desktop, mode);
      await createDraft(page, locale);
      if (mode === 'unknownOther') fixture.state.rows.push(definition('other-before-post', 'other_before_post', 'Synthetic new'));
      const rowsBeforePost = fixture.state.rows.map(d => d.definition_id);
      await save(page, locale);
      await waitTrue(page, `${text('owners.save.unknownCreate', locale)} || !!(${button('settings.taskAttributes.editor.retry', locale)})`, 'POST settles');
      if (mode === 'unknownOther') {
        check('only other-tab row exists; our POST has no effect', fixture.state.rows.map(d => d.definition_id), rowsBeforePost);
        check('other-tab identical row is present', fixture.state.rows.filter(d => d.name === 'Synthetic new').map(d => d.definition_id), ['other-before-post']);
      }
      check('unknown copy', await waitTrue(page, text('owners.save.unknownCreate', locale), 'unknown'), true);
      check('save disabled', await evaluate(page, `(${button('settings.taskAttributes.editor.saveCreate', locale)}).disabled`), true);
      check('ack disabled before observation', await evaluate(page, `(${button('owners.save.acknowledgeUnknown', locale)}).disabled`), true);
      await evaluate(page, `document.querySelector('#ta-name').focus()`);
      await enter(page); await save(page, locale);
      await pause(100); // settle any implicit form activation before checking the hold
      check('unknown Enter keeps acknowledgement disabled', await evaluate(page, `(${button('owners.save.acknowledgeUnknown', locale)}).disabled`), true);
      check('no Retry for unknown create', await evaluate(page, `!!(${button('settings.taskAttributes.editor.retry', locale)})`), false);
      check('unknown Enter/Save sends nothing', fixture.state.posts.length, 1);
      const panel = mode === 'unknownOther' || mode === 'unknownDelayed' || mode === 'unknown502Gate';
      if (panel) {
        await clickSrc(page, button('settings.taskAttributes.editor.cancel', locale));
        check('shared hold panel', await waitTrue(page, `!!document.querySelector('[data-owner-gate="hold"]')`, 'hold panel'), true);
        await evaluate(page, `new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))`);
        await evaluate(page, `Promise.all(document.querySelector('[data-owner-gate="hold"]').getAnimations({subtree:true}).filter(a => a.effect.getComputedTiming().iterations !== Infinity).map(a => a.finished.catch(() => undefined))).then(() => true)`);
      }
      const scope = panel ? `document.querySelector('[data-owner-gate="hold"]')` : editor;
      // An unanswered/failed Check has no acknowledgement authority.
      fixture.state.listFailure = true;
      if (panel) check('Check list receives pointer input', await evaluate(page, `(() => { const e = (${button('owners.save.checkList', locale, scope)}); e.scrollIntoView({block:'center'}); const r = e.getBoundingClientRect(); const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2); return hit === e || e.contains(hit); })()`), true);
      await clickSrc(page, button('owners.save.checkList', locale, scope));
      const failedCopy = tr(locale, panel ? 'owners.save.checkFailed' : 'settings.taskAttributes.editor.refreshFailed');
      check('failed list read completed', await waitTrue(page, `(${scope}).textContent.includes(${JSON.stringify(failedCopy)})`, 'failed list read'), true);
      check('failed check retains disabled acknowledgement', await evaluate(page, `(${button('owners.save.acknowledgeUnknown', locale, scope)}).disabled`), true);
      fixture.state.listFailure = false;
      await clickSrc(page, button('owners.save.checkList', locale, scope));
      check('successful Check enables acknowledge', await waitTrue(page, `!(${button('owners.save.acknowledgeUnknown', locale, scope)}).disabled`, 'observed'), true);
      check('list inspection visible in this panel', await evaluate(page, `!!(${scope}).querySelector('[data-testid="ta-checked-list"]')`), true);
      check('still explicitly unknown', await evaluate(page, text('owners.save.unknownCreate', locale)), true);
      check('no list-derived created notice', await evaluate(page, `document.body.textContent.includes(${JSON.stringify(tr(locale, 'settings.taskAttributes.list.created', { v: 1 }))})`), false);
      check('one POST after checks', fixture.state.posts.length, 1);
      fixture.state.delayed?.(); fixture.state.delayed = null;
      if (!panel) await geometry(page, `unknown-${mode}-${tag}`, locale, width);
      else {
        check('hold content does not clip horizontally', await evaluate(page, `[...document.querySelectorAll('[data-owner-gate="hold"] ul, [data-owner-gate="hold"] li, [data-owner-gate="hold"] [data-testid="ta-checked-list"]')].every(e => e.scrollWidth <= e.clientWidth + 1)`), true);
        check('hold content stays inside clipping ancestor', await evaluate(page, `(() => { const g = document.querySelector('[data-owner-gate="hold"]'); const b = g.getBoundingClientRect(); return [...g.querySelectorAll('ul, li, li p, [data-testid="ta-checked-list"]')].every(e => { const r = e.getBoundingClientRect(); return r.left >= b.left - 1 && r.right <= b.right + 1; }); })()`), true);
        check('hold panel fits viewport', await evaluate(page, `(() => { const r = document.querySelector('[data-owner-gate="hold"]').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1 && r.top >= 0 && r.bottom <= innerHeight + 1; })()`), true);
        await capture(page, `unknown-${mode}-${tag}`, { locale, width, desktop, panel });
      }
      await clickSrc(page, button('owners.save.acknowledgeUnknown', locale, scope));
      check('ack closes owner/editor/gate', await waitTrue(page, `!document.querySelector('#ta-name') && !document.querySelector('[data-owner-gate]')`, 'ack close'), true);
      check('ack never resends', fixture.state.posts.length, 1);
      check('ack never claims saved', await evaluate(page, `document.body.textContent.includes(${JSON.stringify(tr(locale, 'settings.taskAttributes.list.created', { v: 1 }))})`), false);
      await closePage(page); endCase();
    }
    if (selected.has('W05')) for (const state of ['ready', 'exhausted', 'pending', 'unknownCommit']) {
      beginCase(`W05-${state}-${tag}`, 'locale changes preserve draft/node/focus/selection and original action without requests');
      const page = await open(locale, width, desktop, state === 'ready' ? 'success' : state);
      await createDraft(page, locale, 'Draft 🧭 retained');
      if (state !== 'ready') {
        await save(page, locale);
        const stateKey = state === 'pending' ? 'settings.taskAttributes.editor.saving'
          : state === 'exhausted' ? 'settings.taskAttributes.editor.err.keyExhausted' : 'owners.save.unknownCreate';
        check('affected state reached', await waitTrue(page, text(stateKey, locale), state), true);
      }
      await evaluate(page, `window.tadName = document.querySelector('#ta-name'); tadName.focus(); tadName.setSelectionRange(1, 5); window.tadDialog = ${editor}; true;`);
      await geometry(page, `draft-${state}-${tag}`, locale, width);
      for (const next of [locale === 'en' ? 'zh-CN' : 'en', locale]) {
        const before = ledger.length;
        await crossTabSwitch(page, next);
        check('same nodes, draft, focus, selection', await evaluate(page, `[tadName === document.querySelector('#ta-name'), tadDialog === (${editor}), tadName.value, document.activeElement === tadName, tadName.selectionStart, tadName.selectionEnd]`), [true, true, 'Draft 🧭 retained', true, 1, 5]);
        check('locale sends no API request', ledger.length, before);
      }
      if (state === 'unknownCommit') {
        await clickSrc(page, button('owners.save.checkList', locale));
        await waitTrue(page, `!(${button('owners.save.acknowledgeUnknown', locale)}).disabled`, 'ack after check');
        await clickSrc(page, button('owners.save.acknowledgeUnknown', locale));
      } else if (state === 'pending') fixture.state.delayed();
      else {
        fixture.state.mode = 'success';
        await save(page, locale);
      }
      check('original action still settles/closes', await waitTrue(page, `!document.querySelector('#ta-name')`, 'post-switch action'), true);
      check('only deliberate mutations', fixture.state.posts.length, state === 'exhausted' ? 2 : 1);
      await closePage(page); endCase();
    }
  }
}
