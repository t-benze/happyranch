/**
 * B3 generic chain reviewer choice (Settings ▸ Organization) against the
 * ORDINARY production SPA bundle. Driven by `w4a-browser-evidence.mjs
 * --slice reviewer-settings [--reviewer-case <id>]`; every /api request lands in
 * the host's server-side ledger, and PUT bodies are recorded verbatim.
 *
 * The fixture is server-side only (a stateful synthetic daemon): the shipping
 * app receives ordinary GET/PUT Settings, agents and identities responses.
 */
export const OTHER_ORG = 'other-org';
const PERMANENT_IDS = ['lead', 'dev_agent', 'senior_dev'];
const NAMES = { senior_dev: 'Senior' }; // differently named reviewer: label != ID
const MISSING_ID = 'ghost_reviewer';

const CONFIGS = {
  none: { reviewer_agents: [], reviewer_configuration: { state: 'none', missing_agents: [] } },
  configured: { reviewer_agents: ['senior_dev'], reviewer_configuration: { state: 'configured', missing_agents: [] } },
  missing: { reviewer_agents: ['senior_dev', MISSING_ID], reviewer_configuration: { state: 'missing', missing_agents: [MISSING_ID] } },
  invalid: { reviewer_agents: [], reviewer_configuration: { state: 'invalid', missing_agents: [] } },
  other: { reviewer_agents: ['lead'], reviewer_configuration: { state: 'configured', missing_agents: [] } },
};

/** Classify a written list the way the B1 daemon response does. */
function classify(ids) {
  const missing = ids.filter((id) => !PERMANENT_IDS.includes(id));
  return { reviewer_agents: [...ids], reviewer_configuration: { state: ids.length === 0 ? 'none' : missing.length ? 'missing' : 'configured', missing_agents: missing } };
}

export function createReviewerSettingsFixture(baseSettings, baseAgent, org) {
  let store = {};
  let roster = 'ok';
  let put = 'ok';
  const held = [];
  const json = (response, status, body) => {
    response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
    response.end(JSON.stringify(body));
  };
  const snapshot = (slug) => {
    const copy = structuredClone(baseSettings);
    Object.assign(copy.org, structuredClone(store[slug]));
    return copy;
  };
  const agents = () => PERMANENT_IDS.map((name) => ({ ...baseAgent, name, team: 'eng', role: name === 'lead' ? 'manager' : 'worker' }));
  const identities = () => PERMANENT_IDS.map((id) => ({
    canonical_id: id, kind: 'agent', lifecycle: 'active', addressable_name: NAMES[id] ?? id,
    name_revision: 1, canonical_definition_revision: null, naming_status: 'ready',
  }));
  return {
    reset(params) {
      store = {
        [org]: { session_timeout_seconds: null, threads: structuredClone(baseSettings.org.threads), ...structuredClone(CONFIGS[params.get('config') || 'none']) },
        [OTHER_ORG]: { session_timeout_seconds: null, threads: structuredClone(baseSettings.org.threads), ...structuredClone(CONFIGS.other) },
      };
      roster = params.get('roster') || 'ok';
      put = params.get('put') || 'ok';
    },
    setPut(mode) { put = mode; },
    release() { while (held.length) held.shift()(); },
    heldCount: () => held.length,
    handle(request, response, p, row) {
      if (p === '/api/v1/orgs') { json(response, 200, { orgs: [org, OTHER_ORG].map((slug) => ({ slug, root: `/runtime/${slug}` })), broken: [] }); return true; }
      const match = p.match(/^\/api\/v1\/orgs\/([^/]+)\/(settings|settings\/org|agents|identities)$/);
      if (!match || !store[match[1]]) return false;
      const [, slug, leaf] = match;
      if (leaf === 'agents') {
        if (roster === 'loading') return 'hang';
        if (roster === 'error') { json(response, 503, { detail: 'synthetic roster failure' }); return true; }
        json(response, 200, { agents: roster === 'empty' ? [] : agents() }); return true;
      }
      if (leaf === 'identities') { json(response, 200, { identities: roster === 'empty' ? [] : identities() }); return true; }
      if (leaf === 'settings') { json(response, 200, snapshot(slug)); return true; }
      if (request.method !== 'PUT') return false;
      let raw = '';
      request.on('data', (chunk) => { raw += chunk; });
      request.on('end', () => {
        row.body = JSON.parse(raw);
        const finish = () => {
          if (put === 'fail') { json(response, 503, { detail: 'synthetic settings write failure' }); return; }
          const patch = row.body;
          if ('reviewer_agents' in patch) Object.assign(store[slug], classify(patch.reviewer_agents));
          if ('session_timeout_seconds' in patch) store[slug].session_timeout_seconds = patch.session_timeout_seconds;
          if (patch.threads) Object.assign(store[slug].threads, patch.threads);
          json(response, 200, snapshot(slug));
        };
        if (put === 'hold') held.push(finish); else finish();
      });
      return true;
    },
  };
}

const quote = JSON.stringify;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const FIELD = `document.querySelector('[data-testid="reviewer-agents-field"]')`;
const TRIGGER = `${FIELD}?.querySelector('button[role="combobox"]')`;
const button = (label) => `[...document.querySelectorAll('button')].find((b) => b.textContent.trim() === ${quote(label)})`;
const option = (label) => `[...document.querySelectorAll('[role="option"]')].find((o) => o.textContent.trim() === ${quote(label)})`;
const removeButton = (id) => `${FIELD}?.querySelector('li[data-reviewer-id=${quote(id)}] button')`;
const TIMEOUT_INPUT = (tr, locale) => `document.querySelector('input[placeholder=${quote(tr(locale, 'settings.organization.threads.timeoutPlaceholder'))}]')`;

/** Observable field state: ids, labels, notices, picker state, geometry. */
const STATE = (tr, locale) => `(() => {
  const field = ${FIELD};
  if (!field) return { present: false };
  const trigger = ${TRIGGER};
  const r = field.getBoundingClientRect();
  const chips = [...field.querySelectorAll('li[data-reviewer-id]')];
  return {
    present: true,
    ids: chips.map((li) => li.dataset.reviewerId),
    labels: chips.map((li) => li.querySelector('span').textContent),
    unavailable: chips.filter((li) => li.querySelectorAll('span').length > 1).map((li) => li.dataset.reviewerId),
    notices: [...field.querySelectorAll('[role="status"],[role="alert"]')].map((n) => n.textContent.trim()),
    pickerDisabled: Boolean(trigger?.disabled),
    dirty: Boolean(${button(tr(locale, 'settings.organization.saveBar.save'))}),
    fieldInViewportWidth: r.left >= -1 && r.right <= innerWidth + 1,
    chipsContained: chips.every((li) => { const b = li.getBoundingClientRect(); return b.left >= r.left - 1 && b.right <= r.right + 1; }),
    documentOverflow: document.documentElement.scrollWidth > innerWidth + 1,
    lang: document.documentElement.lang,
  };
})()`;

export async function runReviewerSettingsCases(h, { org, fixture, selected = 'all' }) {
  const { openPage, closePage, evaluate, waitTrue, check, beginCase, endCase, capture, cdp, tr, base, seedLocale, chineseNavigator, ledger, crossTabSwitch, clickSrc } = h;
  const want = (id) => selected === 'all' || selected.split(',').includes(id);
  const path = (slug = org) => `/orgs/${slug}/settings/organization`;
  const state = (page, locale) => evaluate(page, STATE(tr, locale));
  const puts = (from) => ledger.slice(from).filter((row) => row.method === 'PUT').map((row) => ({ path: row.path, body: row.body }));
  const hasKey = (body, key) => Boolean(body) && Object.prototype.hasOwnProperty.call(body, key);
  async function key(page, name, code) {
    // Enter carries text so a native <button> is activated like a real key press.
    for (const type of ['keyDown', 'keyUp']) await cdp.send('Input.dispatchKeyEvent', { type, key: name, code: name, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, ...(type === 'keyDown' && name === 'Enter' ? { text: '\r' } : {}) }, page.sessionId);
  }
  async function open(config, { locale = 'en', width = 1440, roster = 'ok', put = 'ok', slug = org } = {}) {
    fixture.reset(new URLSearchParams({ config, roster, put }));
    const page = await openPage(`${base}${path(slug)}`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height: width === 390 ? 844 : 900 });
    const ready = await waitTrue(page, `Boolean(${FIELD})`, 'reviewer field');
    if (roster === 'ok' || roster === 'empty') await waitTrue(page, `Boolean(${TRIGGER}) && !${TRIGGER}.disabled`, 'reviewer roster ready');
    return { page, ready };
  }
  async function pick(page, label) {
    await clickSrc(page, TRIGGER);
    if (!(await waitTrue(page, `Boolean(${option(label)})`, `option ${label}`, 5000))) return false;
    await clickSrc(page, option(label));
    await waitTrue(page, `!document.querySelector('[role="listbox"]')`, 'listbox closed', 5000);
    await pause(200);
    return true;
  }
  async function save(page, locale = 'en') {
    const from = ledger.length;
    await clickSrc(page, button(tr(locale, 'settings.organization.saveBar.save')));
    await waitTrue(page, `!${button(tr(locale, 'settings.organization.saveBar.saving'))}`, 'save settled', 8000);
    await pause(400);
    return from;
  }
  const NONE = (locale) => tr(locale, 'settings.organization.reviewer.none');
  const MISSING = (locale) => tr(locale, 'settings.organization.reviewer.missing', { agents: MISSING_ID });
  const LABEL = { senior_dev: 'Senior · senior_dev', dev_agent: 'dev_agent', lead: 'lead', [MISSING_ID]: MISSING_ID };

  // ── R1: every server/roster state, both locales, both viewports ──
  const VIEWS = [
    { id: 'none', config: 'none', ids: [], picker: true, notices: (l) => [NONE(l)] },
    { id: 'configured', config: 'configured', ids: ['senior_dev'], picker: true, notices: () => [] },
    { id: 'missing', config: 'missing', ids: ['senior_dev', MISSING_ID], unavailable: [MISSING_ID], picker: true, notices: (l) => [`${MISSING(l)}${tr(l, 'settings.organization.reviewer.missingRepair')}`] },
    { id: 'invalid', config: 'invalid', ids: [], picker: true, notices: (l) => [tr(l, 'settings.organization.reviewer.invalid')] },
    { id: 'roster-loading', config: 'configured', roster: 'loading', ids: ['senior_dev'], picker: false, notices: (l) => [tr(l, 'settings.organization.reviewer.loading')] },
    { id: 'roster-error', config: 'configured', roster: 'error', ids: ['senior_dev'], picker: false, notices: (l) => [tr(l, 'settings.organization.reviewer.error')] },
    { id: 'roster-empty', config: 'none', roster: 'empty', ids: [], picker: true, notices: (l) => [NONE(l)] },
  ];
  for (const view of VIEWS) {
    if (!want(`R1-${view.id}`)) continue;
    beginCase(`R1-${view.id}`, `reviewer ${view.id} state en/zh-CN at 1440x900 and 390x844`);
    for (const locale of ['en', 'zh-CN']) for (const width of [1440, 390]) {
      const label = `${view.id} ${locale} ${width}`;
      const { page, ready } = await open(view.config, { locale, width, roster: view.roster });
      check(`${label} field mounted`, ready, true);
      if (view.roster === 'error') await waitTrue(page, `${FIELD}.textContent.includes(${quote(tr(locale, 'settings.organization.reviewer.error'))})`, 'roster error notice');
      const s = await state(page, locale);
      check(`${label} <html lang>`, s.lang, locale);
      check(`${label} retained permanent IDs in saved order`, s.ids, view.ids);
      check(`${label} labels use current names`, s.labels, view.ids.map((id) => view.roster === 'loading' || view.roster === 'error' ? (id === 'senior_dev' ? LABEL.senior_dev : id) : LABEL[id]));
      check(`${label} unavailable chips`, s.unavailable, view.unavailable ?? []);
      check(`${label} exact notices`, s.notices, view.notices(locale));
      check(`${label} never shown as none unless server says none`, s.notices.includes(NONE(locale)), view.config === 'none');
      check(`${label} picker ${view.picker ? 'enabled' : 'disabled'}`, s.pickerDisabled, !view.picker);
      check(`${label} not dirty on load`, s.dirty, false);
      check(`${label} field and chips inside viewport`, [s.fieldInViewportWidth, s.chipsContained, s.documentOverflow], [true, true, false]);
      if (view.id === 'roster-empty') {
        await clickSrc(page, TRIGGER);
        await waitTrue(page, `document.querySelectorAll('[role="option"]').length > 0`, 'options');
        check(`${label} no-agent picker offers only None`, await evaluate(page, `[...document.querySelectorAll('[role="option"]')].map((o) => o.textContent.trim())`), [tr(locale, 'settings.organization.reviewer.noneOption')]);
        check(`${label} no-agent copy`, await evaluate(page, `${FIELD}.textContent.includes(${quote(tr(locale, 'settings.organization.reviewer.noAgents'))})`), true);
        await key(page, 'Escape', 27); await pause(200);
      }
      await evaluate(page, `${FIELD}.scrollIntoView({ block: 'center' }); true`);
      await capture(page, `rv-${view.id}-${locale}-${width}`, { locale, viewport: `${width}x${width === 390 ? 844 : 900}`, state: view.id });
      await closePage(page);
    }
    endCase();
  }

  // ── R2: pick by pointer, ID-vs-label, save, post-save readback ──
  if (want('R2-pick-save')) {
    beginCase('R2-pick-save', 'pick a differently named reviewer from none, save ID, readback');
    const { page } = await open('none');
    const from = ledger.length;
    check('picked Senior', await pick(page, LABEL.senior_dev), true);
    let s = await state(page, 'en');
    check('draft chip is permanent ID with current label', [s.ids, s.labels], [['senior_dev'], [LABEL.senior_dev]]);
    check('dirty bar shown', s.dirty, true);
    check('pending save copy names the ID', s.notices, [tr('en', 'settings.organization.reviewer.pendingSave', { agents: 'senior_dev' })]);
    check('no request before Save', puts(from), []);
    await evaluate(page, `${FIELD}.scrollIntoView({ block: 'center' }); true`);
    await capture(page, 'rv-pick-dirty-en-1440', { locale: 'en', viewport: '1440x900', state: 'picked Senior, dirty' });
    const saveFrom = await save(page);
    const written = puts(saveFrom);
    check('exactly one PUT to own org', written.map((w) => w.path), [`/api/v1/orgs/${org}/settings/org`]);
    check('PUT sends permanent ID, not label', written[0]?.body?.reviewer_agents, ['senior_dev']);
    s = await state(page, 'en');
    check('post-save field re-synced from response', [s.ids, s.dirty, s.notices], [['senior_dev'], false, []]);
    await capture(page, 'rv-pick-saved-en-1440', { locale: 'en', viewport: '1440x900', state: 'saved Senior' });
    await closePage(page);
    // Readback after a fresh load comes from the stateful daemon (not reset).
    const again = await openPage(`${base}${path()}`, { init: `${seedLocale('en')}\n${chineseNavigator}` });
    await waitTrue(again, `Boolean(${FIELD}) && ${FIELD}.querySelectorAll('li[data-reviewer-id]').length > 0`, 'readback');
    s = await state(again, 'en');
    check('reload readback shows saved ID/label', [s.ids, s.labels, s.notices], [['senior_dev'], [LABEL.senior_dev], []]);
    await closePage(again);
    endCase();
  }

  // ── R3: keyboard add, focus, discard ──
  if (want('R3-keyboard-discard')) {
    beginCase('R3-keyboard-discard', 'keyboard add + remove with focus retention, then Discard sends nothing');
    const { page } = await open('configured', { width: 390 });
    const from = ledger.length;
    await evaluate(page, `${TRIGGER}.focus(); true`);
    check('picker focused', await evaluate(page, `document.activeElement === ${TRIGGER}`), true);
    await key(page, 'Enter', 13);
    await waitTrue(page, `Boolean(document.querySelector('[role="listbox"]'))`, 'listbox open', 5000);
    // Options: None, lead, dev_agent (senior_dev already selected).
    check('options exclude already selected ID', await evaluate(page, `[...document.querySelectorAll('[role="option"]')].map((o) => o.textContent.trim())`), [tr('en', 'settings.organization.reviewer.noneOption'), 'lead', 'dev_agent']);
    for (let i = 0; i < 6 && !(await evaluate(page, `document.activeElement?.textContent?.trim() === 'dev_agent'`)); i++) { await key(page, 'ArrowDown', 40); await pause(80); }
    await key(page, 'Enter', 13);
    await waitTrue(page, `!document.querySelector('[role="listbox"]')`, 'listbox closed', 5000);
    await pause(250);
    let s = await state(page, 'en');
    check('keyboard Enter added dev_agent after saved ID', s.ids, ['senior_dev', 'dev_agent']);
    check('focus returned to picker', await evaluate(page, `document.activeElement === ${TRIGGER}`), true);
    await evaluate(page, `${removeButton('senior_dev')}.focus(); true`);
    check('remove button keyboard focusable with label', await evaluate(page, `document.activeElement === ${removeButton('senior_dev')} && document.activeElement.getAttribute('aria-label')`), tr('en', 'settings.organization.reviewer.remove', { agent: LABEL.senior_dev }));
    await key(page, 'Enter', 13); await pause(250);
    s = await state(page, 'en');
    check('keyboard remove', s.ids, ['dev_agent']);
    check('focus moved to picker after chip unmount', await evaluate(page, `document.activeElement === ${TRIGGER}`), true);
    await evaluate(page, `${FIELD}.scrollIntoView({ block: 'center' }); true`);
    await capture(page, 'rv-keyboard-draft-en-390', { locale: 'en', viewport: '390x844', state: 'keyboard draft, picker focused' });
    await clickSrc(page, button(tr('en', 'settings.organization.saveBar.discard'))); await pause(300);
    s = await state(page, 'en');
    check('Discard restores saved IDs and clears dirty', [s.ids, s.dirty], [['senior_dev'], false]);
    check('zero PUT for pick/remove/discard', puts(from), []);
    await closePage(page);
    endCase();
  }

  // ── R4: explicit None clear ──
  if (want('R4-clear-none')) {
    beginCase('R4-clear-none', 'explicit None clears configured reviewers and sends []');
    const { page } = await open('configured');
    check('picked None', await pick(page, tr('en', 'settings.organization.reviewer.noneOption')), true);
    let s = await state(page, 'en');
    check('draft is empty with pending clear copy', [s.ids, s.notices, s.dirty], [[], [tr('en', 'settings.organization.reviewer.pendingClear')], true]);
    await evaluate(page, `${FIELD}.scrollIntoView({ block: 'center' }); true`);
    await capture(page, 'rv-clear-dirty-en-1440', { locale: 'en', viewport: '1440x900', state: 'None chosen, dirty' });
    const written = puts(await save(page));
    check('PUT sends explicit []', written.map((w) => w.body?.reviewer_agents), [[]]);
    s = await state(page, 'en');
    check('response classified none', [s.ids, s.notices, s.dirty], [[], [NONE('en')], false]);
    await closePage(page);
    endCase();
  }

  // ── R5: unrelated save preserves reviewer setting (missing kept) ──
  if (want('R5-unrelated-save')) {
    beginCase('R5-unrelated-save', 'an unrelated Save sends no reviewer_agents (missing and invalid states)');
    for (const config of ['missing', 'invalid', 'configured']) {
      const { page } = await open(config);
      await evaluate(page, `${TIMEOUT_INPUT(tr, 'en')}.focus(); true`);
      await cdp.send('Input.insertText', { text: '45' }, page.sessionId); await pause(200);
      const written = puts(await save(page));
      check(`${config}: one PUT`, written.length, 1);
      check(`${config}: unrelated field sent`, written[0]?.body?.threads?.invocation_timeout_seconds, 45);
      check(`${config}: reviewer_agents key omitted`, hasKey(written[0]?.body, 'reviewer_agents'), false);
      const s = await state(page, 'en');
      check(`${config}: reviewer state unchanged after save`, s.ids, CONFIGS[config].reviewer_agents);
      if (config === 'missing') check('missing notice still shown', s.notices[0]?.startsWith(MISSING('en')), true);
      await closePage(page);
    }
    endCase();
  }

  // ── R6: deliberate edit keeps missing IDs (never pruned) ──
  if (want('R6-missing-retained')) {
    beginCase('R6-missing-retained', 'adding to a missing configuration sends the missing ID verbatim; deliberate removal repairs');
    let { page } = await open('missing');
    check('picked dev_agent', await pick(page, 'dev_agent'), true);
    let written = puts(await save(page));
    check('missing ID retained in deliberate write', written.map((w) => w.body?.reviewer_agents), [['senior_dev', MISSING_ID, 'dev_agent']]);
    let s = await state(page, 'en');
    check('still missing after save', [s.ids, s.unavailable], [['senior_dev', MISSING_ID, 'dev_agent'], [MISSING_ID]]);
    await closePage(page);
    ({ page } = await open('missing'));
    await clickSrc(page, removeButton(MISSING_ID)); await pause(200);
    written = puts(await save(page));
    check('deliberate removal sends repaired list', written.map((w) => w.body?.reviewer_agents), [['senior_dev']]);
    s = await state(page, 'en');
    check('repaired: configured, no notice', [s.ids, s.notices], [['senior_dev'], []]);
    await closePage(page);
    endCase();
  }

  // ── R7: invalid replaced deliberately ──
  if (want('R7-invalid-replace')) {
    beginCase('R7-invalid-replace', 'invalid state: explicit None is a deliberate [] replacement');
    const { page } = await open('invalid');
    check('picked None', await pick(page, tr('en', 'settings.organization.reviewer.noneOption')), true);
    check('None on invalid is dirty', (await state(page, 'en')).dirty, true);
    const written = puts(await save(page));
    check('PUT sends []', written.map((w) => w.body?.reviewer_agents), [[]]);
    check('now none', (await state(page, 'en')).notices, [NONE('en')]);
    await closePage(page);
    endCase();
  }

  // ── R8: save failure keeps draft; retry succeeds ──
  if (want('R8-save-failure')) {
    beginCase('R8-save-failure', 'write failure keeps the draft and dirty state; explicit retry saves');
    const { page } = await open('none', { put: 'fail' });
    await pick(page, LABEL.senior_dev);
    let written = puts(await save(page));
    check('failed PUT carried the draft', written.map((w) => w.body?.reviewer_agents), [['senior_dev']]);
    let s = await state(page, 'en');
    check('draft and dirty kept after failure', [s.ids, s.dirty], [['senior_dev'], true]);
    check('failure banner shown', await evaluate(page, `document.body.textContent.includes(${quote(tr('en', 'settings.organization.saveFailed', { detail: '' }).trim())})`), true);
    await evaluate(page, `${FIELD}.scrollIntoView({ block: 'center' }); true`);
    await capture(page, 'rv-save-failed-en-1440', { locale: 'en', viewport: '1440x900', state: 'save failed, draft kept' });
    // The shared save-failure banner is the subject of this second capture.
    await evaluate(page, `[...document.querySelectorAll('section > div')].find((d) => d.textContent.startsWith(${quote(tr('en', 'settings.organization.saveFailed', { detail: '' }).trim())}))?.scrollIntoView({ block: 'start' }); true`);
    await capture(page, 'rv-save-failed-banner-en-1440', { locale: 'en', viewport: '1440x900', state: 'save failed banner' });
    fixture.setPut('ok');
    written = puts(await save(page));
    s = await state(page, 'en');
    check('retry PUT and success', [written.map((w) => w.body?.reviewer_agents), s.ids, s.dirty], [[['senior_dev']], ['senior_dev'], false]);
    await closePage(page);
    endCase();
  }

  // ── R9: locale switch keeps draft + focus, zero requests ──
  if (want('R9-locale-switch')) {
    beginCase('R9-locale-switch', 'locale switch en->zh-CN->en keeps reviewer draft, node and focus; issues no request');
    for (const width of [1440, 390]) {
      const { page } = await open('missing', { width });
      await pick(page, 'dev_agent');
      await evaluate(page, `${TRIGGER}.focus(); window.__rvField = ${FIELD}; window.__rvTrigger = ${TRIGGER}; true`);
      for (const locale of ['zh-CN', 'en']) {
        const from = ledger.length;
        await crossTabSwitch(page, locale);
        const s = await state(page, locale);
        check(`${width} ->${locale} draft kept`, s.ids, ['senior_dev', MISSING_ID, 'dev_agent']);
        check(`${width} ->${locale} same field/picker node and focus`, await evaluate(page, `window.__rvField === ${FIELD} && window.__rvTrigger === ${TRIGGER} && document.activeElement === window.__rvTrigger`), true);
        check(`${width} ->${locale} localized notices`, s.notices, [`${MISSING(locale)}${tr(locale, 'settings.organization.reviewer.missingRepair')}`, tr(locale, 'settings.organization.reviewer.pendingSave', { agents: `senior_dev, ${MISSING_ID}, dev_agent` })]);
        check(`${width} ->${locale} localized label`, await evaluate(page, `document.getElementById('reviewer-agents-label').textContent`), tr(locale, 'settings.organization.reviewer.label'));
        check(`${width} ->${locale} zero /api requests`, ledger.slice(from), []);
        if (locale === 'zh-CN') { await evaluate(page, `${FIELD}.scrollIntoView({ block: 'center' }); true`); await capture(page, `rv-locale-draft-zh-CN-${width}`, { locale, viewport: `${width}x${width === 390 ? 844 : 900}`, state: 'draft kept after en->zh-CN' }); }
      }
      await closePage(page);
    }
    endCase();
  }

  // ── R10: org switch — drafts and in-flight saves stay with their org ──
  if (want('R10-org-switch')) {
    beginCase('R10-org-switch', 'org switch shows the other org, never carries a draft or late save across orgs');
    // In-app route change (the same router transition the org switcher drives).
    const go = async (page, slug) => {
      await evaluate(page, `(() => { history.pushState({}, '', ${quote(path(slug))}); dispatchEvent(new PopStateEvent('popstate')); return true; })()`);
      await waitTrue(page, `location.pathname === ${quote(path(slug))} && Boolean(${FIELD})`, `route ${slug}`);
      await pause(700);
    };
    const { page } = await open('none');
    const from = ledger.length;
    await pick(page, LABEL.senior_dev);
    await go(page, OTHER_ORG);
    let s = await state(page, 'en');
    check('other org shows its own saved reviewer, no draft', [s.ids, s.dirty], [['lead'], false]);
    await go(page, org);
    s = await state(page, 'en');
    check('returning shows saved state (existing reset-on-org-data ownership)', [s.ids, s.dirty], [[], false]);
    check('no PUT from switching', puts(from), []);
    // In-flight save in org A, switch to org B, then A's response settles.
    fixture.setPut('hold');
    await pick(page, LABEL.senior_dev);
    const saveFrom = ledger.length;
    await clickSrc(page, button(tr('en', 'settings.organization.saveBar.save')));
    for (let i = 0; i < 50 && fixture.heldCount() === 0; i++) await pause(100);
    check('A save is in flight', fixture.heldCount(), 1);
    await go(page, OTHER_ORG);
    fixture.release();
    await pause(1200);
    s = await state(page, 'en');
    check('B field unaffected by A late response', [s.ids, s.dirty, s.notices], [['lead'], false, []]);
    check('A PUT went to A only', puts(saveFrom).map((w) => [w.path, w.body?.reviewer_agents]), [[`/api/v1/orgs/${org}/settings/org`, ['senior_dev']]]);
    await evaluate(page, `${FIELD}.scrollIntoView({ block: 'center' }); true`);
    await capture(page, 'rv-org-switch-other-en-1440', { locale: 'en', viewport: '1440x900', state: 'other org after A late save' });
    await go(page, org);
    s = await state(page, 'en');
    check('A shows its committed save', s.ids, ['senior_dev']);
    await closePage(page);
    endCase();
  }
}
