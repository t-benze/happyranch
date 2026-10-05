/** Bounded W5a reachability regressions against the ordinary routed SPA. */
export const LONG_AGENT = 'agent_with_a_long_raw_identifier_' + '0123456789_'.repeat(6);
export const LONG_TEAM = 'team_with_a_long_raw_identifier_' + 'abcdefghij_'.repeat(6);

// A server-side fixture only; the shipping app receives ordinary API responses.
export function workHoursFixture(path, ref, { org, settings, roster }) {
  if (ref.searchParams.get('workHoursFixture') !== 'long') return undefined;
  if (path === `/api/v1/orgs/${org}/agents`) return { agents: [{ ...roster[0], name: LONG_AGENT, team: LONG_TEAM }] };
  if (path === `/api/v1/orgs/${org}/teams`) return { teams: [{ name: LONG_TEAM, manager: null, workers: [LONG_AGENT] }] };
  if (path === `/api/v1/orgs/${org}/settings`) {
    const copy = structuredClone(settings);
    const wh = copy.org.working_hours;
    wh.teams = { [LONG_TEAM]: wh.teams.eng };
    wh.overrides = { [LONG_AGENT]: wh.overrides.dev_agent };
    return copy;
  }
  return undefined;
}

const quote = JSON.stringify;
const regionName = (locale, detail) => detail
  ? (locale === 'en' ? 'Schedule reconciliation table' : '计划来源核对表')
  : (locale === 'en' ? 'Work Hours roster table' : '工时智能体列表');
const button = (label, scope = 'document') => `[...${scope}.querySelectorAll('button')].find(b => b.textContent.trim() === ${quote(label)})`;
const region = (locale, detail) => `document.querySelector('[role="region"][aria-label="${regionName(locale, detail)}"]')`;
const dialog = `document.querySelector('[role="dialog"][data-state="open"]')`;
const input = `${dialog}?.querySelector('input[placeholder="2h"]')`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function runWorkHoursCases(h, { org, geometryOnly = false, entryOnly = false, longOnly = false }) {
  const { openPage, closePage, evaluate, waitTrue, check, beginCase, endCase, capture, cdp, tr, base, seedLocale, chineseNavigator, ledger, crossTabSwitch } = h;
  async function clickSrc(page, expression) {
    await cdp.send('Page.bringToFront', {}, page.sessionId);
    await evaluate(page, `(${expression}).scrollIntoView({ block: 'center', behavior: 'instant' }); true`);
    // Settle the real scroll position before measuring the pointer target.
    await pause(150);

    await h.clickSrc(page, expression);
  }
  async function key(page, name, code) {
    for (const type of ['keyDown', 'keyUp']) await cdp.send('Input.dispatchKeyEvent', { type, key: name, code: name, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code }, page.sessionId);
  }
  async function geometry(page, locale, width, detail, long) {
    const label = `${detail ? 'detail' : 'overview'} ${locale} ${width}${long ? ' long raw' : ''}`;
    const name = long ? LONG_AGENT : 'dev_agent';
    const team = long ? LONG_TEAM : 'eng';
    check(`${label} table and team fixture ready`, await waitTrue(page, `document.querySelector('table') !== null && document.querySelector('table').textContent.includes(${quote(team)})`, 'table'), true);
    const controls = detail
      ? [tr(locale, 'workHours.editOrgDefault'), tr(locale, 'workHours.editTeam', { team }), tr(locale, 'workHours.detail.editAgent')]
      : [tr(locale, 'workHours.editOrgDefault')];
    const result = await evaluate(page, `(() => {
      const table = document.querySelector('table'); const area = table?.closest('section') || table?.parentElement?.parentElement;
      const bounds = area?.getBoundingClientRect();
      const find = text => [...area.querySelectorAll('button,a')].find(el => el.textContent.trim() === text);
      const nodes = ${quote(controls)}.map(find);
      nodes.push(find(${quote(tr(locale, 'workHours.manageOperatingControl'))}));
      if (!${detail}) nodes.push(area.querySelector('[role="combobox"]'));
      const measured = nodes.map(el => {
        if (!el || !bounds) return { found: false, visible: false };
        const r = el.getBoundingClientRect(); const range = document.createRange(); range.selectNodeContents(el);
        return { found: true, visible: r.width > 8 && r.height > 8 && r.left >= bounds.left - 1 && r.right <= Math.min(bounds.right, innerWidth) + 1 && r.top >= 0 && r.bottom <= innerHeight && el.scrollWidth <= el.clientWidth + 1 && [...range.getClientRects()].every(t => t.left >= r.left - 1 && t.right <= r.right + 1), text: el.textContent.trim(), left: r.left, right: r.right, width: r.width, height: r.height };
      });
      const title = ${detail} ? area.querySelector('h2') : document.querySelector('main h1'); const r = title?.getBoundingClientRect();
      const line = title ? parseFloat(getComputedStyle(title).lineHeight) : 0;
      return { controls: measured, readable: Boolean(r && r.width >= 80 && r.height <= line * (${detail} ? 2.2 : 3.2) && r.left >= 0 && r.right <= innerWidth + 1), documentOverflow: document.documentElement.scrollWidth > innerWidth + 1, headers: [...table.querySelectorAll('th')].map(el => el.textContent.trim()), rawName: ${detail} ? document.querySelector('main h1')?.textContent : [...table.querySelectorAll('a')].find(a => a.textContent === ${quote(name)})?.textContent, rawTeam: table.textContent.includes(${quote(team)}), rawTimezone: table.textContent.includes('America/Los_Angeles') };
    })()`);
    check(`${label} ALL controls present`, result.controls.map(x => x.found), controls.map(() => true).concat(true, ...(detail ? [] : [true])));
    for (const [index, control] of result.controls.entries()) check(`${label} control ${index} text and bounds reachable ${JSON.stringify(control)}`, control.visible, true);
    check(`${label} readable heading`, result.readable, true);
    check(`${label} no document horizontal overflow`, result.documentOverflow, false);
    check(`${label} raw name unchanged`, result.rawName, name);
    check(`${label} raw team and timezone unchanged`, [result.rawTeam, result.rawTimezone], [true, true]);
    check(`${label} ALL table columns`, result.headers, detail
      ? [tr(locale, 'workHours.detail.col.leaf'), tr(locale, 'workHours.provenance.org'), tr(locale, 'workHours.provenance.teamNamed', { team }), tr(locale, 'workHours.provenance.agent'), tr(locale, 'workHours.detail.col.effective')]
      : ['agent', 'team', 'mode', 'cadence', 'on', 'eligibility'].map(k => tr(locale, `workHours.roster.${k}`)));
    const scroll = region(locale, detail);
    const exists = await evaluate(page, `Boolean(${scroll})`);
    check(`${label} localized named scroll region exists`, exists, true);
    if (exists) {
      await evaluate(page, `window.__whRegion = ${scroll}; window.__whRawTable = ${scroll}.querySelector('table').textContent; ${scroll}.focus(); true`);
      check(`${label} scroll region keyboard focusable`, await evaluate(page, `${scroll}.tabIndex === 0 && document.activeElement === ${scroll}`), true);
      const before = await evaluate(page, `${scroll}.scrollLeft`);
      for (let i = 0; i < 80; i++) await key(page, 'ArrowRight', 39);
      await pause(350);
      const reached = await evaluate(page, `(() => {
        const el = ${scroll}; const r = el.getBoundingClientRect(); const table = el.querySelector('table');
        const targets = [table.querySelector('thead tr > :last-child'), table.querySelector('tbody tr > :last-child')];
        return { lastRects: targets.map(cell => { const b = cell.getBoundingClientRect(); return { left: b.left, right: b.right, width: b.width, text: cell.textContent }; }), regionBounds: { left: r.left, right: r.right, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth, scrollLeft: el.scrollLeft }, localOverflow: el.scrollWidth > el.clientWidth, moved: el.scrollLeft > ${before}, atEnd: el.scrollLeft + el.clientWidth >= el.scrollWidth - 2, lastVisible: targets.every(cell => { const b = cell.getBoundingClientRect(); return b.width >= (${detail} ? 80 : 24) && b.right <= r.right + 1 && b.left >= r.left - 1; }), rawKept: table.textContent === window.__whRawTable, focused: document.activeElement === el };
      })()`);
      check(`${label} last effective/eligibility header AND cell visible by keyboard ${JSON.stringify({ cells: reached.lastRects, bounds: reached.regionBounds })}`, reached.lastVisible, true);
      check(`${label} keyboard reaches end`, reached.atEnd, true);
      check(`${label} raw cells and focus kept`, [reached.rawKept, reached.focused], [true, true]);
      if (width === 390) check(`${label} intentional local overflow`, reached.localOverflow, true);
      if (reached.localOverflow) check(`${label} keyboard actually moves scroll`, reached.moved, true);
      else check(`${label} table fits desktop without hidden columns`, width === 1440 && reached.lastVisible, true);
      await capture(page, `wh-${detail ? 'detail' : 'overview'}-${locale}-${width}${long ? '-long' : ''}-last-column`, { locale, viewport: `${width}x${width === 390 ? 844 : 900}`, state: 'named region focused; last column after keyboard scroll' });
      // The region remains mounted and focused when its accessible name changes.
      const from = ledger.length;
      for (const next of [locale === 'en' ? 'zh-CN' : 'en', locale]) {
        await crossTabSwitch(page, next);
        check(`${label} region same node and focus ->${next}`, await evaluate(page, `window.__whRegion === ${region(next, detail)} && document.activeElement === window.__whRegion`), true);
      }
      check(`${label} region switching zero HTTP`, ledger.slice(from), []);
    }
    await evaluate(page, `document.querySelector('table').parentElement.scrollLeft = 0; true`);
    await capture(page, `wh-${detail ? 'detail' : 'overview'}-${locale}-${width}${long ? '-long' : ''}`, { locale, viewport: `${width}x${width === 390 ? 844 : 900}`, state: 'first columns, complete local controls' });
  }
  async function editors(page, locale, width, detail) {
    const label = `${detail ? 'detail' : 'overview'} ${locale} ${width}`;
    const kinds = detail ? ['org', 'team', 'agent'] : ['org', 'team'];
    for (const kind of kinds) {
      if (kind === 'team' && !detail) {
        await clickSrc(page, `document.querySelector('main [role="combobox"]')`);
        await waitTrue(page, `document.querySelector('[role="option"]') !== null`, 'team options');
        await clickSrc(page, `[...document.querySelectorAll('[role="option"]')].find(o => o.textContent.trim() === 'eng')`);
      } else {
        const text = tr(locale, kind === 'org' ? 'workHours.editOrgDefault' : kind === 'team' ? 'workHours.editTeam' : 'workHours.detail.editAgent', { team: 'eng' });
        await clickSrc(page, button(text, `document.querySelector('main')`));
      }
      const title = kind === 'org' ? tr(locale, 'workHours.editOrgDefault') : kind === 'team' ? tr(locale, 'workHours.editTeam', { team: 'eng' }) : tr(locale, 'workHours.tier.titleAgent', { agent: 'dev_agent' });
      await capture(page, `wh-entry-${detail ? 'detail' : 'overview'}-${kind}-${locale}-${width}`, { locale, state: 'actual editor entry before draft input' });

      check(`${label} ${kind} editor opens correct tier`, await waitTrue(page, `${dialog}?.querySelector('h2')?.textContent === ${quote(title)} && Boolean(${input})`, 'correct tier editor'), true);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await clickSrc(page, input);
      await key(page, 'Home', 36);
      // Select existing interval before actual typing, no mutation/write to daemon.
      await evaluate(page, `${input}.select(); true`);
      await cdp.send('Input.insertText', { text: '5h' }, page.sessionId);
      check(`${label} ${kind} literal draft entered`, await evaluate(page, `${input}.value`), '5h');
      await evaluate(page, `window.__whModal = ${dialog}; window.__whInput = ${input}; window.__whTable = document.querySelector('table'); window.__whRoute = location.href; window.__whHistory = history.length; true`);
      const from = ledger.length;
      for (const next of [locale === 'en' ? 'zh-CN' : 'en', locale]) {
        await crossTabSwitch(page, next);
        const nextTitle = kind === 'org' ? tr(next, 'workHours.editOrgDefault') : kind === 'team' ? tr(next, 'workHours.editTeam', { team: 'eng' }) : tr(next, 'workHours.tier.titleAgent', { agent: 'dev_agent' });
        check(`${label} ${kind} modal/control/table/route/history/focus kept ->${next}`, await evaluate(page, `window.__whModal === ${dialog} && window.__whInput === ${input} && window.__whTable === document.querySelector('table') && location.href === window.__whRoute && history.length === window.__whHistory && document.activeElement === window.__whInput`), true);
        check(`${label} ${kind} draft kept ->${next}`, await evaluate(page, `${input}.value`), '5h');
        check(`${label} ${kind} title translates ->${next}`, await evaluate(page, `${dialog}.querySelector('h2').textContent`), nextTitle);
      }
      check(`${label} ${kind} switching zero HTTP/write`, ledger.slice(from), []);
      if (kind !== 'agent') {
        await clickSrc(page, button(tr(locale, 'workHours.dialog.reviewImpact'), dialog));
        check(`${label} ${kind} existing Review impact action works`, await waitTrue(page, `Boolean(${button(tr(locale, 'workHours.dialog.confirmSave'), dialog)})`, 'confirm stage'), true);
        await clickSrc(page, button(tr(locale, 'workHours.dialog.back'), dialog));
        check(`${label} ${kind} Back preserves draft`, await evaluate(page, `${input}.value`), '5h');
      }
      // Agent Save is already owned by TierEditorDialog tests; cancel avoids proof-only writes.
      await clickSrc(page, button(tr(locale, 'common.cancel'), dialog));
      check(`${label} ${kind} existing Cancel action works`, await waitTrue(page, `${dialog} === null`, 'cancel closed'), true);
    }
    await clickSrc(page, `[...document.querySelectorAll('main a')].find(a => a.textContent.trim() === ${quote(tr(locale, 'workHours.manageOperatingControl'))})`);
    check(`${label} Operating-controls link navigates`, await waitTrue(page, `location.pathname === '/orgs/${org}/settings/organization'`, 'Operating controls'), true);
  }
  for (const width of entryOnly || longOnly ? [390] : [390, 1440]) {
    for (const locale of entryOnly ? ['en'] : ['en', 'zh-CN']) {
      for (const detail of entryOnly || longOnly ? [true] : [true, false]) {
        const id = `WH-${detail ? 'detail' : 'overview'}-${locale}-${width}`;
        if (longOnly) {
          beginCase(`${id}-long`, 'actual last-cell readable width at390 with long raw team');
          const page = await openPage(`${base}/orgs/${org}/work-hours/${LONG_AGENT}?workHoursFixture=long`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height: 844 });
          await geometry(page, locale, width, detail, true);
          await closePage(page); endCase(); continue;
        }
        beginCase(id, 'all essential controls and columns reachable; ordinary mounted state');
        const page = await openPage(`${base}/orgs/${org}/work-hours${detail ? '/dev_agent' : ''}`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height: width === 390 ? 844 : 900 });
        await geometry(page, locale, width, detail, false);
        if (!geometryOnly) await editors(page, locale, width, detail);
        await closePage(page); endCase();
        if (!geometryOnly && !entryOnly) {
          beginCase(`${id}-long`, 'long raw names remain verbatim inside the Work Hours feature');
          const longPage = await openPage(`${base}/orgs/${org}/work-hours${detail ? `/${LONG_AGENT}` : ''}?workHoursFixture=long`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height: width === 390 ? 844 : 900 });
          await geometry(longPage, locale, width, detail, true);
          await closePage(longPage); endCase();
        }
      }
    }
  }
}
