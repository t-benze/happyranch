/**
 * THR-305 build-time UI flag rows for `w4a-browser-evidence.mjs --slice feature-flags`.
 *
 * Runs against a real compiled bundle whose own `[features]` build-log line
 * names its profile (`--expect-profile production|local|local-wh-off`). The
 * stateful settings fixture below is server-side only: the shipping app
 * receives ordinary GET/PUT responses. Every phase captures its ledger cursor
 * BEFORE the triggering action and asserts only after a positive settle.
 */
import { readFileSync } from 'node:fs';

export const FF_PROFILES = {
  production: { profile: 'production', work_hours_ui: false, kb_compose_ui: false },
  local: { profile: 'local', work_hours_ui: true, kb_compose_ui: false },
  'local-wh-off': { profile: 'local', work_hours_ui: false, kb_compose_ui: false },
};

/** Parse the single `[features]` line of a build log and check it against the expected profile. */
export function readBuildLog(file, expectName) {
  const expectValues = FF_PROFILES[expectName];
  if (!expectValues) throw new Error(`--expect-profile must be one of ${Object.keys(FF_PROFILES).join('|')}`);
  const lines = readFileSync(file, 'utf8').split('\n').filter((line) => line.includes('[features] profile='));
  if (lines.length !== 1) throw new Error(`build log ${file} must carry exactly one [features] line (found ${lines.length})`);
  const line = lines[0].slice(lines[0].indexOf('[features]')).trim();
  const field = (name) => new RegExp(`(?:^|\\s)${name}=([^\\s(]+)`).exec(line)?.[1];
  const observed = { profile: field('profile'), work_hours_ui: field('work_hours_ui') === 'true', kb_compose_ui: field('kb_compose_ui') === 'true' };
  const matches = Object.entries(expectValues).every(([key, value]) => observed[key] === value);
  return { line, observed, expected: expectValues, matches };
}

const FIXTURE_DETAIL = 'ff fixture raw detail';

/** Server-side stateful settings fixture (GET settings / PUT settings/org) for this slice only. */
export function createFeatureFlagsFixture(settings, org) {
  const initial = structuredClone(settings);
  let state = structuredClone(initial);
  const settingsPath = `/api/v1/orgs/${org}/settings`;
  const writePath = `${settingsPath}/org`;
  const json = (response, status, body) => {
    response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    response.end(JSON.stringify(body));
  };
  return {
    initial,
    get state() { return state; },
    reset(wh) {
      state = structuredClone(initial);
      if (wh === 'disabled') state.org.working_hours.enabled = false;
    },
    /** Returns true when the request was answered here. `row` is the ledger row captured before any await. */
    handle(request, response, path, ref, row) {
      if (request.method === 'GET' && path === settingsPath) { json(response, 200, state); return true; }
      // The generic stub answers unknown paths with {}; the naming UI on
      // Settings needs the real list shape.
      if (request.method === 'GET' && path === `/api/v1/orgs/${org}/identities`) { json(response, 200, { identities: [] }); return true; }
      if (request.method === 'GET') return false;
      let raw = '';
      request.on('data', (chunk) => { raw += chunk; });
      request.on('end', () => {
        try { row.body = raw ? JSON.parse(raw) : null; } catch { row.body = { unparsed: raw }; }
        if (path !== writePath || request.method !== 'PUT') {
          row.unexpected = true;
          json(response, 405, { detail: 'feature-flags slice: unexpected write' });
          return;
        }
        if (ref.searchParams.get('settingsFixture') === 'error') {
          row.status = 422;
          json(response, 422, { detail: FIXTURE_DETAIL });
          return;
        }
        // Approximation for preservation evidence only: shallow-merge the patch.
        const patch = row.body || {};
        const { working_hours: wh, ...rest } = patch;
        state = { ...state, org: { ...state.org, ...rest, ...(wh ? { working_hours: { ...state.org.working_hours, ...wh } } : {}) } };
        row.status = 200;
        json(response, 200, state);
      });
      return true;
    },
  };
}

const quote = JSON.stringify;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function runFeatureFlagsCases(h, { org, expectProfile, buildLog, distIndexSha256 }) {
  const { openPage, closePage, evaluate, waitTrue, clickSrc, check, beginCase, endCase, capture, cdp, tr, base, seedLocale, chineseNavigator, ledger, fixture } = h;
  const expected = FF_PROFILES[expectProfile];
  const wh = expected.work_hours_ui;
  const meta = (extra) => ({ profile: expectProfile, distIndexSha256, ...extra });
  const root = `/orgs/${org}`;
  const settingsUrl = `${root}/settings/organization`;
  const reset = async (whState) => { await fetch(`${base}/__ff_reset${whState ? `?wh=${whState}` : ''}`); };
  const short = (locale) => (locale === 'en' ? 'en' : 'zh-CN');

  const isFeature = (row) => row.path.startsWith(`/api/v1/orgs/${org}/work-hours`) || (row.path === `/api/v1/orgs/${org}/settings/org` && row.body && typeof row.body === 'object' && 'working_hours' in row.body);
  const writes = (rows) => rows.filter((row) => row.method !== 'GET');
  const settingsWrites = (rows) => rows.filter((row) => row.path === `/api/v1/orgs/${org}/settings/org` && row.method !== 'GET');
  const shared = (rows) => [...new Set(rows.filter((row) => row.method === 'GET' && !isFeature(row)).map((row) => row.path))].sort();
  const brief = (row) => ({ method: row.method, path: row.path, search: row.search, ...(row.body !== undefined ? { body: row.body } : {}), ...(row.status ? { status: row.status } : {}), ...(row.unexpected ? { unexpected: true } : {}) });
  /** Bounded observation window after a positive settle, then wait for in-flight bodies. */
  async function observe(from) {
    await pause(600);
    const deadline = Date.now() + 3000;
    while (writes(ledger.slice(from)).some((row) => row.body === undefined) && Date.now() < deadline) await pause(50);
    return ledger.slice(from);
  }

  const navWorkHours = `[...document.querySelectorAll('nav a')].filter(a => (a.getAttribute('href') || '').includes('/work-hours'))`;
  const anyWorkHoursLink = `document.querySelectorAll('a[href*="/work-hours"]').length`;
  const settingsReady = (locale) => `(() => {
    const heading = [...document.querySelectorAll('h1,h2,h3')].some(e => e.textContent.trim() === ${quote(tr(locale, 'settings.panel.organization.title'))});
    const time = [...document.querySelectorAll('input')].some(i => i.value === '02:00');
    return heading && time;
  })()`;
  const switchSel = `document.querySelector('button[role="switch"][aria-labelledby="work-hours-switch-label"]')`;
  const button = (label, scope = 'document') => `[...${scope}.querySelectorAll('button')].find(b => b.textContent.trim() === ${quote(label)})`;
  const openDialog = `document.querySelector('[role="dialog"][data-state="open"]')`;
  const textIn = (text) => `document.body.textContent.includes(${quote(text)})`;
  const whDialogTexts = (locale) => [tr(locale, 'settings.organization.disableDialog.title'), tr(locale, 'workHours.eligibilityEditor.title')];

  beginCase(`FF-BUILD-${expectProfile}`, 'the dist under test is the build whose [features] line matches the expected profile');
  const log = readBuildLog(buildLog, expectProfile);
  check('build log [features] line matches the expected profile', log.matches, true);
  current(h).buildLog = log;
  endCase();
  if (!log.matches) throw new Error(`build log does not match --expect-profile ${expectProfile}: ${log.line}`);

  // ---------------------------------------------------------------- Settings ▸ Organization
  for (const locale of ['en', 'zh-CN']) {
    for (const [width, height] of [[1440, 900], [390, 844]]) {
      const id = `FF-${wh ? 'ON' : 'OFF'}-SETTINGS-${short(locale)}-${width}`;
      beginCase(id, `${expectProfile}: Settings ▸ Organization Work Hours UI ${wh ? 'present' : 'absent'}`);
      await reset();
      const from = ledger.length;
      const page = await openPage(`${base}${settingsUrl}`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height });
      check(`${id} settled on Organization heading and fixture values`, await waitTrue(page, settingsReady(locale), `${id} settle`), true);
      const rows = await observe(from);
      check(`${id} Work Hours nav link ${wh ? 'present' : 'absent'}`, (await evaluate(page, `${navWorkHours}.length`)) > 0, wh);
      check(`${id} operating heading ${wh ? 'present' : 'absent'}`, await evaluate(page, textIn(tr(locale, 'settings.organization.operating.heading'))), wh);
      check(`${id} operating-controls ${wh ? 'present' : 'absent'}`, await evaluate(page, `Boolean(document.querySelector('[data-testid="operating-controls"]'))`), wh);
      check(`${id} Work Hours switch ${wh ? 'present' : 'absent'}`, await evaluate(page, `Boolean(${switchSel})`), wh);
      check(`${id} eligibility Edit ${wh ? 'present' : 'absent'}`, await evaluate(page, `Boolean(${button(tr(locale, 'settings.organization.operating.editEligibility'))})`), wh);
      if (!wh) {
        check(`${id} no /work-hours link anywhere`, await evaluate(page, anyWorkHoursLink), 0);
        check(`${id} no Work Hours dialog copy in the document (portals included)`, await evaluate(page, `[${whDialogTexts(locale).map(quote).join(',')}].some(t => document.body.textContent.includes(t))`), false);
        check(`${id} no open dialog`, await evaluate(page, `Boolean(${openDialog})`), false);
      }
      check(`${id} feature-specific requests`, rows.filter(isFeature).map(brief), []);
      check(`${id} settings writes`, settingsWrites(rows).map(brief), []);
      check(`${id} unexpected writes`, writes(rows).map(brief), []);
      check(`${id} shared reads observed (nonvacuous)`, shared(rows).includes(`/api/v1/orgs/${org}/settings`), true);
      current(h).shared = shared(rows);
      await capture(page, `${id}`, meta({ locale, viewport: `${width}x${height}`, state: 'settings-organization' }));
      await closePage(page);
      endCase();
    }
  }

  // ---------------------------------------------------------------- direct + legacy URLs
  const urls = [
    { n: 'overview', path: `${root}/work-hours` },
    { n: 'wakes', path: `${root}/work-hours?view=wakes` },
    { n: 'detail', path: `${root}/work-hours/dev_agent` },
    { n: 'schedule', path: `${root}/schedule` },
  ];
  const region = (locale, detail) => `document.querySelector('[role="region"][aria-label="${detail
    ? (locale === 'en' ? 'Schedule reconciliation table' : '计划来源核对表')
    : (locale === 'en' ? 'Work Hours roster table' : '工时智能体列表')}"]')`;
  for (const locale of ['en', 'zh-CN']) {
    for (const { n, path } of urls) {
      const id = `FF-${wh ? 'ON' : 'OFF'}-URL-${short(locale)}-${n}`;
      beginCase(id, `${expectProfile}: ${path} ${wh ? 'renders Work Hours' : 'uses the existing not-found route'}`);
      await reset();
      const from = ledger.length;
      const page = await openPage(`${base}${path}`, { init: `${seedLocale(locale)}\n${chineseNavigator}` });
      if (!wh) {
        const notFound = `Boolean([...document.querySelectorAll('main a[href="/"]')].find(a => a.textContent.trim() === ${quote(tr(locale, 'shell.notFound.goHome'))}))`;
        check(`${id} not-found body settled`, await waitTrue(page, notFound, `${id} not-found`), true);
        const before = await evaluate(page, `({ href: location.href, length: history.length })`);
        const rows = await observe(from);
        const after = await evaluate(page, `({ href: location.href, length: history.length })`);
        check(`${id} location unchanged (no redirect)`, after, before);
        check(`${id} location is the requested URL`, after.href, `${base}${path}`);
        check(`${id} no Work Hours region`, await evaluate(page, `Boolean(${region(locale, false)}) || Boolean(${region(locale, true)})`), false);
        check(`${id} no /work-hours link anywhere`, await evaluate(page, anyWorkHoursLink), 0);
        check(`${id} feature-specific requests`, rows.filter(isFeature).map(brief), []);
        check(`${id} unexpected writes`, writes(rows).map(brief), []);
      } else {
        const ready = n === 'overview' ? `Boolean(${region(locale, false)})`
          : n === 'detail' ? `Boolean(${region(locale, true)})`
          : textIn('Reviewed 3 PRs — «raw» summary.');
        check(`${id} Work Hours ${n} rendered`, await waitTrue(page, ready, `${id} ready`), true);
        const rows = await observe(from);
        if (n === 'schedule') check(`${id} legacy redirect to the Wakes view`, await evaluate(page, `location.pathname + location.search`), `${root}/work-hours?view=wakes`);
        else check(`${id} location kept`, await evaluate(page, `location.href`), `${base}${path}`);
        const read = n === 'overview' ? `/api/v1/orgs/${org}/teams` : n === 'detail' ? `/api/v1/orgs/${org}/work-hours/next-wakes` : `/api/v1/orgs/${org}/work-hours`;
        check(`${id} existing page read observed`, rows.some((row) => row.method === 'GET' && row.path === read), true);
        check(`${id} unexpected writes`, writes(rows).map(brief), []);
      }
      await capture(page, id, meta({ locale, viewport: '1440x900', url: path }));
      await closePage(page);
      endCase();
    }
  }

  // Reload and wait for the NEW document, so a settle check cannot pass on the old one.
  // A dirty form raises the existing unsaved-changes beforeunload prompt; it is
  // accepted here and reported to the caller.
  async function reload(page) {
    let prompted = false;
    const loaded = cdp.waitFor('Page.loadEventFired', { sessionId: page.sessionId });
    const dialog = cdp.waitFor('Page.javascriptDialogOpening', { sessionId: page.sessionId, timeout: 5000 })
      .then(async (params) => { prompted = params.type; await cdp.send('Page.handleJavaScriptDialog', { accept: true }, page.sessionId); })
      .catch(() => undefined);
    await cdp.send('Page.reload', { ignoreCache: true }, page.sessionId);
    await loaded;
    await Promise.race([dialog, pause(0)]);
    return prompted;
  }

  // Type into the session-timeout input through real input events.
  async function typeTimeout(page, value) {
    await clickSrc(page, `document.querySelector('input[type="number"][min="1"]')`);
    await evaluate(page, `(() => { const i = document.querySelector('input[type="number"][min="1"]'); i.select(); return true; })()`);
    await cdp.send('Input.insertText', { text: value }, page.sessionId);
    await pause(200);
  }

  if (expectProfile === 'production') {
    // -------------------------------------------------------------- FF-OFF-SAVE: unrelated Save works
    {
      const id = 'FF-OFF-SAVE-en-1440';
      beginCase(id, 'production: general Organization Save is one PUT without working_hours; readback preserves stored Work Hours');
      await reset();
      const page = await openPage(`${base}${settingsUrl}`, { init: `${seedLocale('en')}\n${chineseNavigator}` });
      check(`${id} settled`, await waitTrue(page, settingsReady('en'), `${id} settle`), true);
      await typeTimeout(page, '90');
      check(`${id} save bar shown`, await waitTrue(page, `Boolean(${button(tr('en', 'settings.organization.saveBar.save'))})`, 'save bar'), true);
      // Record whether the general "Saved" banner is ever inserted (from the
      // mutation records themselves, so a transient insertion is not missed).
      await evaluate(page, `(() => { window.__ffSaved = false; const saved = ${quote(tr('en', 'settings.organization.saved'))}; new MutationObserver((records) => { for (const r of records) for (const n of r.addedNodes) if ((n.textContent || '').includes(saved)) window.__ffSaved = true; }).observe(document.body, { subtree: true, childList: true }); return true; })()`);
      const from = ledger.length;
      await clickSrc(page, button(tr('en', 'settings.organization.saveBar.save')));
      check(`${id} save settled (save bar cleared, no error)`, await waitTrue(page, `!${button(tr('en', 'settings.organization.saveBar.save'))} && !document.body.textContent.includes('Save failed')`, 'save settle'), true);
      const rows = await observe(from);
      const put = settingsWrites(rows);
      check(`${id} exactly one settings write`, put.length, 1);
      check(`${id} write is PUT 200`, put.map((row) => [row.method, row.status]), [['PUT', 200]]);
      check(`${id} body keys (general patch only)`, Object.keys(put[0]?.body || {}).sort(), ['dreaming', 'session_timeout_seconds', 'threads']);
      check(`${id} body session_timeout_seconds`, put[0]?.body?.session_timeout_seconds, 90);
      check(`${id} no working_hours in the body`, 'working_hours' in (put[0]?.body || {}), false);
      // Observations, not assertions: the existing org re-sync effect resets the
      // general save state when the response differs from the loaded data
      // (identical on the pre-flag base, WH on). The settled success above is
      // the save-bar clear without an error.
      current(h).generalSavedBanner = { insertedDuringSave: await evaluate(page, 'window.__ffSaved'), visibleAfterSettle: await evaluate(page, textIn(tr('en', 'settings.organization.saved'))) };
      check(`${id} feature-specific requests`, rows.filter(isFeature).map(brief), []);
      current(h).saveLedger = rows.map(brief);
      await capture(page, `${id}-after-save`, meta({ locale: 'en', viewport: '1440x900', state: 'after-save' }));
      // Readback: the same phase continues through a reload of the Organization page.
      await reload(page);
      check(`${id} readback settled`, await waitTrue(page, settingsReady('en'), 'readback settle'), true);
      check(`${id} readback timeout field`, await evaluate(page, `document.querySelector('input[type="number"][min="1"]').value`), '90');
      const all = await observe(from);
      check(`${id} readback: still exactly one write`, settingsWrites(all).length, 1);
      check(`${id} stored working_hours preserved (fixture evidence only)`, fixture.state.org.working_hours, fixture.initial.org.working_hours);
      await capture(page, `${id}-readback`, meta({ locale: 'en', viewport: '1440x900', state: 'readback' }));
      await closePage(page);
      endCase();
    }

    // -------------------------------------------------------------- FF-OFF-SAVE-ERR: general error ownership
    {
      const id = 'FF-OFF-SAVE-ERR-en-1440';
      beginCase(id, 'production: general Save error shows the existing localized failure with API 422; stored state unchanged');
      await reset();
      const page = await openPage(`${base}${settingsUrl}?settingsFixture=error`, { init: `${seedLocale('en')}\n${chineseNavigator}` });
      check(`${id} settled`, await waitTrue(page, settingsReady('en'), `${id} settle`), true);
      await typeTimeout(page, '90');
      await waitTrue(page, `Boolean(${button(tr('en', 'settings.organization.saveBar.save'))})`, 'save bar');
      const from = ledger.length;
      await clickSrc(page, button(tr('en', 'settings.organization.saveBar.save')));
      const failure = tr('en', 'settings.organization.saveFailed', { detail: 'API 422' });
      check(`${id} localized failure shown`, await waitTrue(page, textIn(failure), 'save failure'), true);
      const rows = await observe(from);
      check(`${id} exactly one PUT answered 422`, settingsWrites(rows).map((row) => [row.method, row.status]), [['PUT', 422]]);
      check(`${id} raw fixture detail is not the general owner's copy`, await evaluate(page, textIn(FIXTURE_DETAIL)), false);
      check(`${id} stored state unchanged`, fixture.state, fixture.initial);
      await capture(page, id, meta({ locale: 'en', viewport: '1440x900', state: 'save-error' }));
      current(h).unsavedPromptOnReload = await reload(page);
      check(`${id} readback settled`, await waitTrue(page, settingsReady('en'), 'readback'), true);
      check(`${id} readback timeout still unset`, await evaluate(page, `document.querySelector('input[type="number"][min="1"]').value`), '');
      await closePage(page);
      endCase();
    }

    // -------------------------------------------------------------- FF-OFF-KB
    {
      const id = 'FF-OFF-KB-en-1440';
      beginCase(id, 'production: Knowledge has no Compose action and no POST /kb');
      await reset();
      const from = ledger.length;
      const page = await openPage(`${base}${root}/kb`, { init: `${seedLocale('en')}\n${chineseNavigator}` });
      check(`${id} KB page settled`, await waitTrue(page, textIn(tr('en', 'kb.pageTitle')), 'kb settle'), true);
      const rows = await observe(from);
      check(`${id} no Compose action`, await evaluate(page, `Boolean(${button(tr('en', 'kb.composeButton'))})`), false);
      check(`${id} no POST /kb`, rows.filter((row) => row.method === 'POST' && row.path === `/api/v1/orgs/${org}/kb`).map(brief), []);
      await capture(page, id, meta({ locale: 'en', viewport: '1440x900', state: 'kb' }));
      await closePage(page);
      endCase();
    }
  }

  if (expectProfile === 'local') {
    // -------------------------------------------------------------- FF-ON-SAVE-SWITCH
    {
      const id = 'FF-ON-SAVE-SWITCH-en-1440';
      beginCase(id, 'local: enabling the Work Hours switch saves a working_hours-only PUT; readback checked; schedules preserved');
      await reset('disabled');
      const page = await openPage(`${base}${settingsUrl}`, { init: `${seedLocale('en')}\n${chineseNavigator}` });
      check(`${id} settled with the switch off`, await waitTrue(page, `${settingsReady('en')} && ${switchSel}?.getAttribute('aria-checked') === 'false'`, `${id} settle`), true);
      const from = ledger.length;
      await clickSrc(page, switchSel);
      check(`${id} saved banner`, await waitTrue(page, textIn(tr('en', 'settings.organization.workHoursSaved')), 'wh banner'), true);
      const rows = await observe(from);
      const put = settingsWrites(rows);
      check(`${id} exactly one PUT 200`, put.map((row) => [row.method, row.status]), [['PUT', 200]]);
      check(`${id} body is working_hours.enabled only`, put[0]?.body, { working_hours: { enabled: true } });
      check(`${id} no confirmation dialog on enable`, await evaluate(page, `Boolean(${openDialog})`), false);
      await reload(page);
      check(`${id} readback switch checked`, await waitTrue(page, `${switchSel}?.getAttribute('aria-checked') === 'true'`, 'readback'), true);
      const { enabled: _enabled, ...schedules } = fixture.state.org.working_hours;
      const { enabled: _initial, ...initialSchedules } = fixture.initial.org.working_hours;
      check(`${id} schedules preserved (fixture evidence only)`, schedules, initialSchedules);
      check(`${id} readback: still one write`, settingsWrites(await observe(from)).length, 1);
      await capture(page, id, meta({ locale: 'en', viewport: '1440x900', state: 'switch-enabled-readback' }));
      await closePage(page);
      endCase();
    }

    // -------------------------------------------------------------- FF-ON-SAVE-ELIG
    {
      const id = 'FF-ON-SAVE-ELIG-zh-CN-390';
      const locale = 'zh-CN';
      beginCase(id, 'local: eligibility edit → review → confirm saves working_hours.agents only (zh-CN, 390)');
      await reset();
      const page = await openPage(`${base}${settingsUrl}`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width: 390, height: 844 });
      check(`${id} settled`, await waitTrue(page, settingsReady(locale), `${id} settle`), true);
      const edit = button(tr(locale, 'settings.organization.operating.editEligibility'));
      await clickSrc(page, edit);
      check(`${id} editor open`, await waitTrue(page, `Boolean(${openDialog}) && ${openDialog}.textContent.includes(${quote(tr(locale, 'workHours.eligibilityEditor.title'))})`, 'editor'), true);
      await pause(500); // let the Radix open animation settle before pointer input
      await clickSrc(page, `[...${openDialog}.querySelectorAll('button')].find(b => b.textContent.includes('dev_agent'))`);
      await pause(200);
      await clickSrc(page, button(tr(locale, 'workHours.dialog.reviewImpact'), openDialog));
      check(`${id} impact preview shown`, await waitTrue(page, `Boolean(${button(tr(locale, 'workHours.dialog.confirmSave'), openDialog)})`, 'preview'), true);
      const from = ledger.length;
      await clickSrc(page, button(tr(locale, 'workHours.dialog.confirmSave'), openDialog));
      check(`${id} zh-CN saved banner`, await waitTrue(page, textIn(tr(locale, 'settings.organization.workHoursSaved')), 'banner'), true);
      check(`${id} dialog closed`, await waitTrue(page, `!${openDialog}`, 'dialog closed'), true);
      await pause(500);
      const focused = await evaluate(page, `(() => { const a = document.activeElement; return { tag: a?.tagName, text: (a?.textContent || '').trim().slice(0, 40), isEdit: a === ${edit} }; })()`);
      // Recorded, not asserted: after a confirmed eligibility save the existing
      // dialog leaves focus on <body> (identical on the pre-flag base build).
      current(h).focusAfterClose = focused;
      const rows = await observe(from);
      const put = settingsWrites(rows);
      check(`${id} exactly one PUT 200`, put.map((row) => [row.method, row.status]), [['PUT', 200]]);
      check(`${id} body keys`, [Object.keys(put[0]?.body || {}), Object.keys(put[0]?.body?.working_hours || {})], [['working_hours'], ['agents']]);
      check(`${id} agents patch`, put[0]?.body?.working_hours?.agents, { mode: 'all', include: [], exclude: ['support_bot', 'dev_agent'] });
      await capture(page, id, meta({ locale, viewport: '390x844', state: 'eligibility-saved' }));
      await closePage(page);
      endCase();
    }

    // -------------------------------------------------------------- FF-ON-SAVE-ERR
    {
      const id = 'FF-ON-SAVE-ERR-en-1440';
      beginCase(id, 'local: disable → confirm with a 422 shows the raw Work Hours detail; stored state unchanged; switch stays on');
      await reset();
      const page = await openPage(`${base}${settingsUrl}?settingsFixture=error`, { init: `${seedLocale('en')}\n${chineseNavigator}` });
      check(`${id} settled with the switch on`, await waitTrue(page, `${settingsReady('en')} && ${switchSel}?.getAttribute('aria-checked') === 'true'`, `${id} settle`), true);
      await clickSrc(page, switchSel);
      check(`${id} disable confirmation open`, await waitTrue(page, `Boolean(${openDialog}) && ${openDialog}.textContent.includes(${quote(tr('en', 'settings.organization.disableDialog.title'))})`, 'confirm'), true);
      await pause(500); // let the Radix open animation settle before pointer input
      const from = ledger.length;
      await clickSrc(page, button(tr('en', 'settings.organization.disableDialog.confirm'), openDialog));
      check(`${id} Work Hours alert shows the raw detail`, await waitTrue(page, `[...document.querySelectorAll('[role="alert"]')].some(a => a.textContent === ${quote(FIXTURE_DETAIL)})`, 'wh alert'), true);
      const rows = await observe(from);
      check(`${id} exactly one PUT answered 422`, settingsWrites(rows).map((row) => [row.method, row.status, row.body]), [['PUT', 422, { working_hours: { enabled: false } }]]);
      check(`${id} switch remains on`, await evaluate(page, `${switchSel}?.getAttribute('aria-checked')`), 'true');
      check(`${id} stored state unchanged`, fixture.state, fixture.initial);
      await capture(page, id, meta({ locale: 'en', viewport: '1440x900', state: 'wh-save-error' }));
      await closePage(page);
      endCase();
    }
  }

}

function current(h) {
  return h.currentCase();
}
