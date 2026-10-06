/** Bounded W5a reachability regressions against the ordinary routed SPA. */
export const LONG_AGENT = 'agent_with_a_long_raw_identifier_' + '0123456789_'.repeat(6);
export const LONG_TEAM = 'team_with_a_long_raw_identifier_' + 'abcdefghij_'.repeat(6);
export const LONG_TIMEZONE = 'America/North_Dakota/New_Salem';

// A server-side fixture only; the shipping app receives ordinary API responses.
export function workHoursFixture(path, ref, { org, settings, roster }) {
  if (ref.searchParams.get('workHoursFixture') !== 'long') return undefined;
  if (path === `/api/v1/orgs/${org}/agents`) return { agents: [{ ...roster[0], name: LONG_AGENT, team: LONG_TEAM }] };
  if (path === `/api/v1/orgs/${org}/teams`) return { teams: [{ name: LONG_TEAM, manager: null, workers: [LONG_AGENT] }] };
  if (path === `/api/v1/orgs/${org}/settings`) {
    const copy = structuredClone(settings);
    const wh = copy.org.working_hours;
    wh.teams = { [LONG_TEAM]: wh.teams.eng };
    wh.teams[LONG_TEAM].window.timezone = LONG_TIMEZONE;
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

export async function runWorkHoursCases(h, { org, geometryOnly = false, entryOnly = false, longOnly = false, editorOnly = false, editorRed = false, editorKeyboard = false }) {
  const { openPage, closePage, evaluate, waitTrue, check, beginCase, endCase, capture, cdp, tr, base, seedLocale, chineseNavigator, ledger, crossTabSwitch } = h;
  async function clickSrc(page, expression) {
    await cdp.send('Page.bringToFront', {}, page.sessionId);
    // Page load precedes the routed React/query mount. Editor-only cases must
    // establish their own target readiness rather than depend on geometry().
    const ready = await waitTrue(page, `Boolean(${expression})`, `pointer target ${expression}`);
    if (!ready) throw new Error(`Work Hours pointer target not mounted: ${expression}`);
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
      return { controls: measured, readable: Boolean(r && r.width >= 80 && r.height <= line * (${detail} ? 2.2 : 3.2) && r.left >= 0 && r.right <= innerWidth + 1), documentOverflow: document.documentElement.scrollWidth > innerWidth + 1, headers: [...table.querySelectorAll('th')].map(el => el.textContent.trim()), rawName: ${detail} ? document.querySelector('main h1')?.textContent : [...table.querySelectorAll('a')].find(a => a.textContent === ${quote(name)})?.textContent, rawTeam: table.textContent.includes(${quote(team)}), rawTimezone: table.textContent.includes(${quote(long ? LONG_TIMEZONE : 'America/Los_Angeles')}) };
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
  async function editorBounds(page, label, shot, locale, windowed) {
    // Query the actual mounted children, never a CSS class or document-width
    // proxy. Scroll each target inside the real dialog before measuring it.
    const count = await evaluate(page, `(() => {
      const d = ${dialog}; if (!d) return null;
      window.__whBoundControls = [...d.querySelectorAll('input,button,[role="combobox"]')];
      window.__whBoundText = [...d.querySelectorAll('h2,p,span')].filter(el =>
        el.textContent.trim() && !el.closest('[aria-hidden="true"]') &&
        getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0);
      return { controls: window.__whBoundControls.length, text: window.__whBoundText.length,
        inputs: d.querySelectorAll('input').length, selects: d.querySelectorAll('[role="combobox"]').length,
        days: [...d.querySelectorAll('button[aria-pressed]')].map(b => b.textContent),
        switchCount: d.querySelectorAll('[role="switch"]').length,
        closeCount: d.querySelectorAll('button[aria-label]').length };
    })()`);
    const impact = windowed === null;
    check(`${label} editor controls/text non-vacuous`, Boolean(count && count.controls >= (impact ? 3 : 6) && count.text >= (impact ? 3 : 5)), true);
    check(`${label} editor actual mode fields`, count && [count.inputs, count.selects, count.days, count.switchCount, count.closeCount],
      [impact ? 0 : windowed ? 3 : 1, impact ? 0 : 2, windowed ? ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] : [], impact ? 0 : 1, 1]);
    if (!count) return;
    for (const [kind, size] of [['Controls', count.controls], ['Text', count.text]]) {
      for (let i = 0; i < size; i++) {
        const expression = `window.__whBound${kind}[${i}]`;
        await evaluate(page, `${expression}.scrollIntoView({ block: 'center', behavior: 'instant' }); true`);
        await pause(50);
        const measured = await evaluate(page, `(() => {
          const d = ${dialog}, el = ${expression}, r = el.getBoundingClientRect(), b = d.getBoundingClientRect();
          const left = Math.max(0, b.left + d.clientLeft), right = Math.min(innerWidth, b.left + d.clientLeft + d.clientWidth);
          const top = Math.max(0, b.top + d.clientTop), bottom = Math.min(innerHeight, b.top + d.clientTop + d.clientHeight);
          const range = document.createRange(); range.selectNodeContents(el);
          const rects = [...range.getClientRects()].filter(x => x.width > 0 && x.height > 0);
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return { text: el.textContent.trim(), value: el.value ?? null, tag: el.tagName, role: el.getAttribute('role'),
            bounds: { left: r.left, right: r.right, top: r.top, bottom: r.bottom }, content: { left, right, top, bottom }, scrollTop: d.scrollTop,
            fits: r.width > 0 && r.height > 0 && r.left >= left - 1 && r.right <= right + 1 && r.top >= top - 1 && r.bottom <= bottom + 1,
            textFits: rects.every(x => x.left >= left - 1 && x.right <= right + 1 && x.top >= top - 1 && x.bottom <= bottom + 1) && el.scrollWidth <= el.clientWidth + 1,
            pointer: hit === el || el.contains(hit), disabled: Boolean(el.disabled) };
        })()`);
        check(`${label} editor ${kind} ${i} inside content/viewport ${JSON.stringify(measured)}`, measured.fits && measured.textFits, true);
        if (kind === 'Controls') check(`${label} editor control ${i} real pointer target`, measured.pointer && !measured.disabled, true);
      }
    }
    // A full Tab cycle proves every control is keyboard reachable. .focus()
    // only establishes the cycle start; membership is recorded after real Tab.
    await evaluate(page, `window.__whBoundControls[0].focus(); window.__whTabSeen = new Set(); true`);
    // Native time inputs can use more than one Tab stop for their segments.
    for (let i = 0; i < count.controls * 4 + 2; i++) {
      await key(page, 'Tab', 9);
      const complete = await evaluate(page, `window.__whTabSeen.add(document.activeElement); window.__whBoundControls.every(el => window.__whTabSeen.has(el))`);
      if (complete) break;
    }
    check(`${label} editor ALL controls reached by keyboard Tab`, await evaluate(page, `window.__whBoundControls.map(el => window.__whTabSeen.has(el))`), Array(count.controls).fill(true));
    const scroll = await evaluate(page, `(() => { const d = ${dialog}; return { top: d.scrollTop, scrollHeight: d.scrollHeight, clientHeight: d.clientHeight, scrollWidth: d.scrollWidth, clientWidth: d.clientWidth }; })()`);
    check(`${label} editor no horizontal clipping`, scroll.scrollWidth <= scroll.clientWidth + 1, true);
    await evaluate(page, `${dialog}.scrollTop = 0; true`);
    await capture(page, `${shot}-top`, { locale, state: 'actual dialog top; all children individually measured/scrolled' });
    if (scroll.scrollHeight > scroll.clientHeight + 1) {
      await evaluate(page, `${dialog}.scrollTop = ${dialog}.scrollHeight; true`);
      check(`${label} editor actual vertical scroll reaches footer`, await evaluate(page, `${dialog}.scrollTop > 0 && ${dialog}.scrollTop + ${dialog}.clientHeight >= ${dialog}.scrollHeight - 2`), true);
      await capture(page, `${shot}-bottom`, { locale, state: 'actual dialog bottom after vertical scrolling' });
    }
  }
  async function editors(page, locale, width, detail, long = false) {
    const label = `${detail ? 'detail' : 'overview'} ${locale} ${width}${long ? ' long raw' : ''}`;
    const team = long ? LONG_TEAM : 'eng', agent = long ? LONG_AGENT : 'dev_agent';
    const kinds = editorRed || editorKeyboard ? ['agent'] : detail ? ['org', 'team', 'agent'] : ['org', 'team'];
    for (const kind of kinds) {
      if (kind === 'team' && !detail) {
        await clickSrc(page, `document.querySelector('main [role="combobox"]')`);
        await waitTrue(page, `document.querySelector('[role="option"]') !== null`, 'team options');
        await clickSrc(page, `[...document.querySelectorAll('[role="option"]')].find(o => o.textContent.trim() === ${quote(team)})`);
      } else {
        const text = tr(locale, kind === 'org' ? 'workHours.editOrgDefault' : kind === 'team' ? 'workHours.editTeam' : 'workHours.detail.editAgent', { team });
        await clickSrc(page, button(text, `document.querySelector('main')`));
      }
      const title = kind === 'org' ? tr(locale, 'workHours.editOrgDefault') : kind === 'team' ? tr(locale, 'workHours.editTeam', { team }) : tr(locale, 'workHours.tier.titleAgent', { agent });
      const shot = `wh-entry-${detail ? 'detail' : 'overview'}-${kind}-${locale}-${width}${long ? '-long' : ''}`;

      check(`${label} ${kind} editor opens correct tier`, await waitTrue(page, `${dialog}?.querySelector('h2')?.textContent === ${quote(title)} && Boolean(${input})`, 'correct tier editor'), true);
      await editorBounds(page, `${label} ${kind} windowed`, `${shot}-windowed`, locale, true);
      // Old-build RED measures the actual agent children without subsequent
      // pointer actions that are themselves clipped by the known old defect.
      if (editorRed) continue;
      if (long && kind === 'agent') check(`${label} full raw inherited timezone/provenance`, await evaluate(page, `${dialog}.textContent.includes(${quote(LONG_TIMEZONE)}) && ${dialog}.textContent.includes(${quote(LONG_TEAM)})`), true);
      // Drive the real Select with keyboard into continuous mode, then back.
      const modeControl = `${dialog}.querySelector('[role="combobox"]')`;
      await clickSrc(page, modeControl);
      await waitTrue(page, `Boolean(document.querySelector('[role="option"]'))`, 'mode options');
      check(`${label} ${kind} mode option keyboard focus ready`, await waitTrue(page,
        `document.activeElement?.getAttribute('role') === 'option'`, 'mode option focus'), true);
      await key(page, 'End', 35);
      // Radix defers End's focus move. Enter must act on the observed option,
      // rather than racing that move and selecting the previous windowed item.
      const continuousFocused = await waitTrue(page,
        `document.activeElement?.getAttribute('role') === 'option' && document.activeElement.textContent.trim() === 'continuous'`,
        'continuous option keyboard focus');
      check(`${label} ${kind} End focuses actual continuous option`, continuousFocused, true);
      if (!continuousFocused) throw new Error(`${label} ${kind}: End did not focus continuous`);
      await key(page, 'Enter', 13);
      check(`${label} ${kind} real keyboard continuous option`, await waitTrue(page, `${modeControl}.textContent.includes('continuous') && ${dialog}.querySelectorAll('input[type="time"]').length === 0`, 'continuous fields'), true);
      await editorBounds(page, `${label} ${kind} continuous`, `${shot}-continuous`, locale, false);
      await clickSrc(page, modeControl);
      await waitTrue(page, `Boolean(document.querySelector('[role="option"]'))`, 'windowed option');
      await clickSrc(page, `[...document.querySelectorAll('[role="option"]')].find(o => o.textContent.trim() === 'windowed')`);
      check(`${label} ${kind} windowed restores day/time controls`, await waitTrue(page, `${dialog}.querySelectorAll('input[type="time"]').length === 2`, 'windowed fields'), true);
      await cdp.send('Page.bringToFront', {}, page.sessionId);
      await clickSrc(page, input);
      await key(page, 'Home', 36);
      // Select existing interval before actual typing, no mutation/write to daemon.
      await evaluate(page, `${input}.select(); true`);
      await cdp.send('Input.insertText', { text: '5h' }, page.sessionId);
      check(`${label} ${kind} literal draft entered`, await evaluate(page, `${input}.value`), '5h');
      await evaluate(page, `${input}.setSelectionRange(0, 1); true`);
      await evaluate(page, `window.__whModal = ${dialog}; window.__whInput = ${input}; window.__whTable = document.querySelector('table'); window.__whRoute = location.href; window.__whHistory = history.length; true`);
      const from = ledger.length;
      for (const next of [locale === 'en' ? 'zh-CN' : 'en', locale]) {
        await crossTabSwitch(page, next);
        const nextTitle = kind === 'org' ? tr(next, 'workHours.editOrgDefault') : kind === 'team' ? tr(next, 'workHours.editTeam', { team }) : tr(next, 'workHours.tier.titleAgent', { agent });
        check(`${label} ${kind} modal/control/table/route/history/focus kept ->${next}`, await evaluate(page, `window.__whModal === ${dialog} && window.__whInput === ${input} && window.__whTable === document.querySelector('table') && location.href === window.__whRoute && history.length === window.__whHistory && document.activeElement === window.__whInput`), true);
        check(`${label} ${kind} draft kept ->${next}`, await evaluate(page, `${input}.value`), '5h');
        check(`${label} ${kind} selection kept ->${next}`, await evaluate(page, `[${input}.selectionStart, ${input}.selectionEnd]`), [0, 1]);
        check(`${label} ${kind} title translates ->${next}`, await evaluate(page, `${dialog}.querySelector('h2').textContent`), nextTitle);
      }
      check(`${label} ${kind} switching zero HTTP/write`, ledger.slice(from), []);
      if (kind !== 'org') {
        const reset = button(tr(locale, 'workHours.tier.reset'), `${input}.parentElement`);
        await clickSrc(page, reset);
        check(`${label} ${kind} actual reset returns to inherited`, await evaluate(page, `${input}.value`), '');
        await clickSrc(page, input);
        await cdp.send('Input.insertText', { text: '5h' }, page.sessionId);
        check(`${label} ${kind} draft reentered after reset`, await evaluate(page, `${input}.value`), '5h');
      }
      if (kind !== 'agent') {
        await clickSrc(page, button(tr(locale, 'workHours.dialog.reviewImpact'), dialog));
        check(`${label} ${kind} existing Review impact action works`, await waitTrue(page, `Boolean(${button(tr(locale, 'workHours.dialog.confirmSave'), dialog)})`, 'confirm stage'), true);
        await editorBounds(page, `${label} ${kind} impact`, `${shot}-impact`, locale, null);
        await clickSrc(page, button(tr(locale, 'workHours.dialog.back'), dialog));
        check(`${label} ${kind} Back preserves draft`, await evaluate(page, `${input}.value`), '5h');
      }
      // Agent Save is already owned by TierEditorDialog tests; cancel avoids proof-only writes.
      await clickSrc(page, button(tr(locale, 'common.cancel'), dialog));
      check(`${label} ${kind} existing Cancel action works`, await waitTrue(page, `${dialog} === null`, 'cancel closed'), true);
    }
    if (editorRed) return;
    await clickSrc(page, `[...document.querySelectorAll('main a')].find(a => a.textContent.trim() === ${quote(tr(locale, 'workHours.manageOperatingControl'))})`);
    check(`${label} Operating-controls link navigates`, await waitTrue(page, `location.pathname === '/orgs/${org}/settings/organization'`, 'Operating controls'), true);
  }
  for (const width of entryOnly || longOnly || editorRed || editorKeyboard ? [390] : [390, 1440]) {
    for (const locale of editorRed || editorKeyboard ? ['zh-CN'] : entryOnly ? ['en'] : ['en', 'zh-CN']) {
      for (const detail of entryOnly || longOnly || editorRed || editorKeyboard ? [true] : [true, false]) {
        const id = `WH-${detail ? 'detail' : 'overview'}-${locale}-${width}`;
        if (longOnly) {
          beginCase(`${id}-long`, 'actual last-cell readable width at390 with long raw team');
          const page = await openPage(`${base}/orgs/${org}/work-hours/${LONG_AGENT}?workHoursFixture=long`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height: 844 });
          await geometry(page, locale, width, detail, true);
          await closePage(page); endCase(); continue;
        }
        beginCase(id, 'all essential controls and columns reachable; ordinary mounted state');
        const page = await openPage(`${base}/orgs/${org}/work-hours${detail ? '/dev_agent' : ''}`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height: width === 390 ? 844 : 900 });
        if (!editorOnly && !editorRed) await geometry(page, locale, width, detail, false);
        if (!geometryOnly) await editors(page, locale, width, detail);
        await closePage(page); endCase();
        if (!geometryOnly && !entryOnly && !editorRed && !editorKeyboard) {
          beginCase(`${id}-long`, 'long raw names remain verbatim inside the Work Hours feature');
          const longPage = await openPage(`${base}/orgs/${org}/work-hours${detail ? `/${LONG_AGENT}` : ''}?workHoursFixture=long`, { init: `${seedLocale(locale)}\n${chineseNavigator}`, width, height: width === 390 ? 844 : 900 });
          if (!editorOnly) await geometry(longPage, locale, width, detail, true);
          if (!geometryOnly) await editors(longPage, locale, width, detail, true);
          await closePage(longPage); endCase();
        }
      }
    }
  }
}
