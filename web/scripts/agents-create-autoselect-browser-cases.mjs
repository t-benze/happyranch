/**
 * THR-402 Create team / Create agent vs. the Agents page's first-roster
 * auto-selection, for `w4a-browser-evidence.mjs --slice agents-create`
 * (evidence tooling only; not a required check, not wired into CI).
 *
 * Venue: the real built SPA on the real `/orgs/:slug/agents` route, served
 * same-origin next to a synthetic `/api/v1` stub. The fixture holds the first
 * roster GET until a case releases it, so the roster settles AFTER the
 * founder opened creation and typed a draft (the TASK-19430 defect: the
 * settle's unsolicited navigation hit the dirty draft's owner gate, whose
 * prompt then intercepted the Create click — zero POST). Every assertion
 * reads the URL, the DOM the founder sees, or the server-side ledger.
 *
 *   AC-W   dirty Worker draft, late roster -> no navigation/prompt; one POST
 *   AC-T   dirty Create team manager draft, same
 *   AC-AUTO creation closed -> the first agent's detail is still auto-selected
 *   AC-CLOSE creation open across the settle, then closed -> no replayed navigation
 *   AC-MGR  Create agent -> Manager of a new team -> "Team created" + Create agent in that team
 *   AC-TEAMS team-list read fails after the create -> named explanation + Refresh (reads only)
 *   AC-LOC  locale switch while the create is pending -> the computer label follows the locale
 */
const ROSTER = [
  { name: 'dev_agent', team: 'eng', role: 'worker', executor: 'claude', description: 'Builds features', repos: {}, system_prompt: 'You build features.' },
  { name: 'lead', team: 'eng', role: 'manager', executor: 'claude', description: 'Leads engineering', repos: {}, system_prompt: 'You lead the eng team.' },
];
const identityOf = (agent) => ({
  canonical_id: agent.name, kind: 'agent', lifecycle: 'active', addressable_name: agent.addressable_name ?? agent.name,
  name_revision: 1, canonical_definition_revision: null, naming_status: 'ready',
});

export function createAgentsCreateFixture(org) {
  const prefix = `/api/v1/orgs/${org}`;
  const state = { gated: false, held: [], agents: [], teams: [], posts: [], teamsFailAfterCreate: false, holdPost: false, heldPosts: [] };
  const send = (response, status, body) => {
    response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    response.end(JSON.stringify(body));
  };
  const reset = ({ gated, teamsFailAfterCreate = false, holdPost = false }) => {
    state.gated = gated;
    state.teamsFailAfterCreate = teamsFailAfterCreate;
    state.holdPost = holdPost;
    state.heldPosts = [];
    state.held = [];
    state.agents = ROSTER.map((agent) => ({ ...agent }));
    state.teams = [
      { name: 'default', manager: null, manager_kind: 'human', human_manager: 'founder', is_default: true, workers: [] },
      { name: 'eng', manager: 'lead', manager_kind: 'agent', workers: ['dev_agent'] },
    ];
    state.posts = [];
  };
  reset({ gated: false });
  /** Answer every held roster GET with the current roster; later GETs answer at once. */
  const release = () => {
    state.gated = false;
    for (const response of state.held.splice(0)) send(response, 200, { agents: state.agents });
  };
  function handle(request, response, p, url, row) {
    if (p === `${prefix}/agents` && request.method === 'POST') {
      let raw = '';
      request.on('data', (chunk) => { raw += chunk; });
      request.on('end', () => {
        const body = JSON.parse(raw);
        row.body = body;
        state.posts.push(body);
        // A held create stays pending (no answer) for the rest of the case.
        if (state.holdPost) { state.heldPosts.push(response); row.held = true; return; }
        const id = body.display_name.toLowerCase().replaceAll('-', '_');
        const team = body.team ?? body.new_team;
        state.agents.push({ name: id, addressable_name: body.display_name, team, role: body.role, executor: body.executor, description: body.description, repos: {}, system_prompt: body.system_prompt });
        if (body.new_team) state.teams.push({ name: body.new_team, manager: id, manager_kind: 'agent', workers: [] });
        send(response, 200, { name: id, canonical_id: id, addressable_name: body.display_name, team, role: body.role });
      });
      return true;
    }
    if (p === `${prefix}/agents` && request.method === 'GET') {
      if (state.gated) { state.held.push(response); row.held = true; return true; }
      send(response, 200, { agents: state.agents });
      return true;
    }
    if (p === `${prefix}/teams`) {
      if (state.teamsFailAfterCreate && state.posts.length > 0) { send(response, 500, { detail: 'synthetic team-list read failure' }); return true; }
      send(response, 200, { teams: state.teams });
      return true;
    }
    if (p === `${prefix}/identities`) {
      send(response, 200, { identities: [...state.agents.map(identityOf), { canonical_id: 'founder', kind: 'founder', lifecycle: 'founder', addressable_name: 'Founder', name_revision: 1, canonical_definition_revision: null, naming_status: 'ready' }], naming_diagnostic: null });
      return true;
    }
    // The created agent's detail pane reads, shaped like the base roster's rows.
    if (/\/agents\/[a-z0-9_]+\/memory\/entries\/$/.test(p)) { send(response, 200, { entries: [] }); return true; }
    if (/\/agents\/[a-z0-9_]+\/cleanup-activity$/.test(p)) { send(response, 200, { activities: [] }); return true; }
    if (/\/agents\/[a-z0-9_]+\/knowledge\/imports$/.test(p)) { send(response, 200, { operations: [] }); return true; }
    if (p === `${prefix}/knowledge/archives`) { send(response, 200, { archives: [] }); return true; }
    const detail = new RegExp(`^${prefix}/agents/([a-z0-9_]+)$`).exec(p);
    if (detail && request.method === 'GET') {
      const agent = state.agents.find((entry) => entry.name === detail[1]);
      if (agent) { send(response, 200, agent); return true; }
    }
    return false;
  }
  return { handle, reset, release, state };
}

export async function runAgentsCreateCases(h, { org, only = '' }) {
  const { openPage, closePage, capture, check, beginCase, endCase, evaluate, waitTrue, clickSrc, cdp, ledger, base, tr, seedLocale, fixture, crossTabSwitch } = h;
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  const selected = only ? new Set(only.split(',')) : null;
  const agentsPath = `/orgs/${org}/agents`;
  const pathIs = (path) => `location.pathname === ${JSON.stringify(path)}`;
  const buttonByText = (text, scope = 'document') => `[...${scope}.querySelectorAll('button')].find((b) => b.textContent.trim() === ${JSON.stringify(text)} && !b.closest('[role="dialog"],[role="alertdialog"]'))`;
  const SUBMIT = `document.querySelector('button[type="submit"][form="create-agent-form"]')`;
  const observe = `(() => ({
    path: location.pathname,
    prompt: document.querySelectorAll('[role="alertdialog"]').length,
    form: !!document.querySelector('#agent-name'),
    name: document.querySelector('#agent-name')?.value ?? null,
  }))()`;
  const rosterPosts = () => ledger.filter((row) => row.method === 'POST' && row.path === `/api/v1/orgs/${org}/agents`);

  async function type(page, selector, text) {
    // Focus the field, then type through real input events (insertText).
    await evaluate(page, `(() => { const el = document.querySelector(${JSON.stringify(selector)}); el.scrollIntoView({ block: 'center' }); el.focus(); return document.activeElement === el; })()`);
    await cdp.send('Input.insertText', { text }, page.sessionId);
  }
  async function openAgents(locale, width, height, gated, options = {}) {
    fixture.reset({ gated, ...options });
    const page = await openPage(`${base}${agentsPath}`, { init: seedLocale(locale), width, height });
    await waitTrue(page, `!!(${buttonByText(tr(locale, 'agents.page.createAgent'))})`, 'Agents header actions');
    return page;
  }
  async function heldRosterThenRelease(id, page) {
    const deadline = Date.now() + 5000;
    while (fixture.state.held.length === 0 && Date.now() < deadline) await pause(50);
    check(`${id} first roster GET is held (unanswered) until this point`, fixture.state.held.length > 0, true);
    fixture.release();
    await pause(1200); // the settle and any navigation it issues land well inside this window
  }

  const combos = [['zh-CN', 1440, 900], ['en', 390, 844]];
  for (const [locale, width, height] of combos) {
    const tag = `${locale}-${width}`;

    for (const mode of ['agent', 'team']) {
      const id = `${mode === 'agent' ? 'AC-W' : 'AC-T'}-${tag}`;
      if (selected && !selected.has(mode === 'agent' ? 'AC-W' : 'AC-T')) continue;
      beginCase(id, `${mode === 'agent' ? 'dirty Worker draft' : 'dirty Create team manager draft'}, first roster released late: no navigation or prompt; one ordinary submit sends one POST`);
      const page = await openAgents(locale, width, height, true);
      await clickSrc(page, buttonByText(tr(locale, mode === 'agent' ? 'agents.page.createAgent' : 'agents.page.createTeam')));
      check(`${id} creation form opened`, await waitTrue(page, `!!document.querySelector('#agent-name')`, 'form'), true);
      const name = mode === 'agent' ? 'Research-Bot' : 'Growth-Lead';
      await pause(300); // the dialog's open animation and focus settle, as for a person
      await type(page, '#agent-name', name);
      if (mode === 'team') check(`${id} team name suggested from the manager name`, await waitTrue(page, `document.querySelector('#agent-new-team')?.value === 'growth'`, 'suggestion', 3000), true);
      if (mode === 'agent') {
        // THR-402 v1.1: the team is an explicit radio card, not a <select>.
        await waitTrue(page, `!!document.querySelector('input[name="create-team"][value="eng"]')`, 'team options');
        await evaluate(page, `(() => { document.querySelector('input[name="create-team"][value="eng"]').click(); return true; })()`);
      }
      await type(page, '#agent-description', 'Synthetic draft');
      await type(page, '#agent-system-prompt', 'You are a synthetic draft.');
      check(`${id} Create is enabled before the roster settles`, await waitTrue(page, `${SUBMIT} && !${SUBMIT}.disabled`, 'submit enabled', 5000), true);
      const postsBefore = rosterPosts().length;
      await heldRosterThenRelease(id, page);
      check(`${id} after settle: same route, no unsaved-changes prompt, draft intact`, await evaluate(page, observe), { path: agentsPath, prompt: 0, form: true, name });
      await capture(page, `agents-create-${mode}-late-roster-${tag}`, { locale, viewport: `${width}x${height}`, state: 'dirty draft after late first roster' });
      await clickSrc(page, SUBMIT);
      const expected = name.toLowerCase().replaceAll('-', '_');
      const navigated = await waitTrue(page, pathIs(`${agentsPath}/${expected}`), 'success navigation', 8000);
      await pause(500);
      const posts = rosterPosts().slice(postsBefore);
      check(`${id} one ordinary submit sends exactly one POST`, posts.length, 1);
      check(`${id} POST carries the draft (${mode === 'agent' ? 'team' : 'new_team'})`, posts.map((row) => ({ display_name: row.body?.display_name, role: row.body?.role, team: row.body?.team ?? null, new_team: row.body?.new_team ?? null })),
        [{ display_name: name, role: mode === 'agent' ? 'worker' : 'manager', team: mode === 'agent' ? 'eng' : null, new_team: mode === 'agent' ? null : 'growth' }]);
      check(`${id} explicit success navigation to the created agent is preserved`, navigated, true);
      check(`${id} the created agent's page renders (no page error)`, await waitTrue(page, `!document.body.textContent.includes(${JSON.stringify(tr(locale, 'shell.error.title'))}) && document.body.textContent.includes(${JSON.stringify(expected)})`, 'created detail', 5000), true);
      await capture(page, `agents-create-${mode}-created-${tag}`, { locale, viewport: `${width}x${height}`, state: 'created after one POST', posts: posts.length });
      await closePage(page);
      endCase();
    }

    if (!selected || selected.has('AC-AUTO')) {
      const id = `AC-AUTO-${tag}`;
      beginCase(id, 'creation closed: the first roster settle still auto-selects the first agent');
      const page = await openAgents(locale, width, height, true);
      check(`${id} still on the bare route while the roster is held`, await evaluate(page, pathIs(agentsPath)), true);
      await heldRosterThenRelease(id, page);
      check(`${id} first agent's detail auto-selected`, await waitTrue(page, pathIs(`${agentsPath}/dev_agent`), 'auto-select', 5000), true);
      await capture(page, `agents-create-autoselect-closed-${tag}`, { locale, viewport: `${width}x${height}`, state: 'auto-selected first agent' });
      await closePage(page);
      endCase();
    }

    if (!selected || selected.has('AC-CLOSE')) {
      const id = `AC-CLOSE-${tag}`;
      beginCase(id, 'creation open across the settle, then closed: the suppressed auto-selection is not replayed');
      const page = await openAgents(locale, width, height, true);
      await clickSrc(page, buttonByText(tr(locale, 'agents.page.createAgent')));
      await waitTrue(page, `!!document.querySelector('#agent-name')`, 'form');
      await heldRosterThenRelease(id, page);
      check(`${id} creation still open on the bare route after settle`, await evaluate(page, observe), { path: agentsPath, prompt: 0, form: true, name: '' });
      const cancel = `[...document.querySelectorAll('[role="dialog"] button')].find((b) => b.textContent.trim() === ${JSON.stringify(tr(locale, 'common.cancel'))})`;
      if (await evaluate(page, `!!(${cancel})`)) await clickSrc(page, cancel);
      await pause(1200);
      check(`${id} after closing: no dialog, no prompt, still the bare route`, await evaluate(page, observe), { path: agentsPath, prompt: 0, form: false, name: null });
      await capture(page, `agents-create-closed-after-settle-${tag}`, { locale, viewport: `${width}x${height}`, state: 'creation closed after suppressed settle' });
      await closePage(page);
      endCase();
    }

    // Fill a Manager (new team) draft: the name drives the suggested team key.
    async function fillManager(page, id, name, key) {
      await pause(300);
      await type(page, '#agent-name', name);
      check(`${id} team key suggested from the manager name`, await waitTrue(page, `document.querySelector('#agent-new-team')?.value === ${JSON.stringify(key)}`, 'suggestion', 3000), true);
      await type(page, '#agent-description', 'Synthetic manager draft');
      await type(page, '#agent-system-prompt', 'You are a synthetic manager.');
    }
    const completionText = `document.querySelector('[data-testid="create-completion"]')?.textContent ?? ''`;
    const FOLLOW_ON = `document.querySelector('[data-testid="create-completion-follow-on"]')`;
    const REFRESH = `document.querySelector('[data-testid="create-completion-refresh"]')`;

    if (!selected || selected.has('AC-MGR')) {
      const id = `AC-MGR-${tag}`;
      beginCase(id, 'Create agent -> Manager of a new team: the completion follows what was created (Team created + Create agent in that team), not the entry action');
      const page = await openAgents(locale, width, height, false);
      await clickSrc(page, buttonByText(tr(locale, 'agents.page.createAgent')));
      await waitTrue(page, `!!document.querySelector('#create-role-manager')`, 'role choice');
      await evaluate(page, `(() => { document.querySelector('#create-role-manager').click(); return true; })()`);
      await fillManager(page, id, 'Growth-Lead', 'growth');
      const postsBefore = rosterPosts().length;
      await clickSrc(page, SUBMIT);
      check(`${id} navigated to the created manager`, await waitTrue(page, pathIs(`${agentsPath}/growth_lead`), 'success navigation', 8000), true);
      check(`${id} completion panel shown`, await waitTrue(page, `!!document.querySelector('[data-testid="create-completion"]')`, 'completion', 5000), true);
      check(`${id} one POST, manager of new_team growth`, rosterPosts().slice(postsBefore).map((row) => [row.body?.role, row.body?.new_team ?? null]), [['manager', 'growth']]);
      check(`${id} heading says Team created (not Agent created)`, await evaluate(page, `document.querySelector('#create-completion-heading')?.textContent.trim()`), tr(locale, 'agents.create.done.teamTitle'));
      check(`${id} body names the team and its manager (no worker copy)`, await evaluate(page, `(${completionText}).includes(${JSON.stringify(tr(locale, 'agents.create.done.teamBody', { team: 'growth', name: 'Growth-Lead' }))})`), true);
      check(`${id} follow-on Create agent in growth is offered and enabled`, await waitTrue(page, `${FOLLOW_ON} && !${FOLLOW_ON}.disabled && ${FOLLOW_ON}.textContent.includes(${JSON.stringify(tr(locale, 'agents.create.success.addToTeam', { team: 'growth' }))})`, 'follow-on', 5000), true);
      await capture(page, `agents-create-manager-from-agent-${tag}`, { locale, viewport: `${width}x${height}`, state: 'manager created through Create agent' });
      if (await evaluate(page, `!!${FOLLOW_ON}`)) await clickSrc(page, FOLLOW_ON);
      check(`${id} follow-on opens Create agent with growth preselected, no POST`, [await waitTrue(page, `document.querySelector('input[name="create-team"]:checked')?.value === 'growth'`, 'preselect', 5000), rosterPosts().length - postsBefore], [true, 1]);
      await closePage(page);
      endCase();
    }

    if (!selected || selected.has('AC-TEAMS')) {
      const id = `AC-TEAMS-${tag}`;
      beginCase(id, 'team-list read fails after a confirmed create: the completion explains it and Refresh re-reads (never creates), restoring the follow-on');
      const page = await openAgents(locale, width, height, false, { teamsFailAfterCreate: true });
      await clickSrc(page, buttonByText(tr(locale, 'agents.page.createTeam')));
      await waitTrue(page, `!!document.querySelector('#agent-name')`, 'form');
      await fillManager(page, id, 'Growth-Lead', 'growth');
      await clickSrc(page, SUBMIT);
      check(`${id} navigated to the created manager`, await waitTrue(page, pathIs(`${agentsPath}/growth_lead`), 'success navigation', 8000), true);
      check(`${id} creation stays confirmed (Team created)`, await waitTrue(page, `document.querySelector('#create-completion-heading')?.textContent.trim() === ${JSON.stringify(tr(locale, 'agents.create.done.teamTitle'))}`, 'heading', 5000), true);
      check(`${id} localized team-list explanation shown`, await waitTrue(page, `document.querySelector('[data-testid="create-completion-teams"]')?.textContent.trim() === ${JSON.stringify(tr(locale, 'agents.create.done.teamsUnavailableTeam', { team: 'growth' }))}`, 'explanation', 5000), true);
      check(`${id} Refresh offered; follow-on disabled and described by the explanation`, await evaluate(page, `[!!${REFRESH}, ${FOLLOW_ON}?.disabled, ${FOLLOW_ON}?.getAttribute('aria-describedby')]`), [true, true, 'create-completion-teams']);
      await capture(page, `agents-create-teams-read-failed-${tag}`, { locale, viewport: `${width}x${height}`, state: 'team list read failed after create' });
      fixture.state.teamsFailAfterCreate = false;
      const mark = ledger.length;
      const postsBefore = rosterPosts().length;
      if (await evaluate(page, `!!${REFRESH}`)) await clickSrc(page, REFRESH);
      check(`${id} after Refresh the follow-on is enabled and the explanation is gone`, await waitTrue(page, `${FOLLOW_ON} && !${FOLLOW_ON}.disabled && !document.querySelector('[data-testid="create-completion-teams"]')`, 'refresh recovery', 5000), true);
      const since = ledger.slice(mark);
      check(`${id} Refresh read the team list and roster with GET only (no POST)`, [since.some((row) => row.method === 'GET' && row.path === `/api/v1/orgs/${org}/teams`), since.some((row) => row.method === 'GET' && row.path === `/api/v1/orgs/${org}/agents`), since.filter((row) => row.method !== 'GET').length, rosterPosts().length - postsBefore], [true, true, 0, 0]);
      await capture(page, `agents-create-teams-refreshed-${tag}`, { locale, viewport: `${width}x${height}`, state: 'after read-only Refresh' });
      await closePage(page);
      endCase();
    }

    if (!selected || selected.has('AC-LOC')) {
      const id = `AC-LOC-${tag}`;
      const other = locale === 'en' ? 'zh-CN' : 'en';
      beginCase(id, 'locale switch while the create is pending: the submitted computer label is rendered in the new locale; one POST, owner kept');
      const page = await openAgents(locale, width, height, false, { holdPost: true });
      await clickSrc(page, buttonByText(tr(locale, 'agents.page.createAgent')));
      await waitTrue(page, `!!document.querySelector('input[name="create-team"][value="eng"]')`, 'team options');
      await pause(300);
      await type(page, '#agent-name', 'Research-Bot');
      await evaluate(page, `(() => { document.querySelector('input[name="create-team"][value="eng"]').click(); return true; })()`);
      await type(page, '#agent-description', 'Synthetic draft');
      await type(page, '#agent-system-prompt', 'You are a synthetic draft.');
      const postsBefore = rosterPosts().length;
      await clickSrc(page, SUBMIT);
      const ctx = `document.querySelector('[data-testid="create-context"]')?.textContent ?? ''`;
      check(`${id} pending: context names ${locale} This computer`, await waitTrue(page, `(${ctx}).includes(${JSON.stringify(tr(locale, 'owners.computer.thisComputer'))})`, 'pending context', 5000), true);
      await crossTabSwitch(page, other);
      const after = await evaluate(page, `(() => { const c = ${ctx}; return [c.includes(${JSON.stringify(tr(other, 'owners.computer.thisComputer'))}), c.includes(${JSON.stringify(tr(locale, 'owners.computer.thisComputer'))})]; })()`);
      check(`${id} after switch to ${other}: computer label in ${other}, none left in ${locale}`, after, [true, false]);
      check(`${id} still exactly one POST (pending, not replayed)`, [rosterPosts().length - postsBefore, fixture.state.heldPosts.length], [1, 1]);
      await capture(page, `agents-create-pending-locale-switch-${tag}`, { locale: other, viewport: `${width}x${height}`, state: `pending create after ${locale}->${other}` });
      await closePage(page);
      endCase();
    }
  }
}
