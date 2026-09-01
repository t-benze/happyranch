import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

interface Row {
  skill_id: string;
  name: string;
  type: 'managed' | 'system_contract' | 'user_authored';
  system_contract: boolean;
  visibility_category: 'toggleable' | 'read_only';
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  assigned_agent_count: number;
  effective_agent_count: number;
  has_assigned_not_yet_effective: boolean;
  summary: string;
  source: string;
  policy_class: string;
  status: string;
  version: string;
}

function row(over: Partial<Row> & Pick<Row, 'skill_id' | 'name' | 'type'>): Row {
  return {
    system_contract: false,
    visibility_category: 'toggleable',
    validation_state: 'validated',
    assigned_agent_count: 0,
    effective_agent_count: 0,
    has_assigned_not_yet_effective: false,
    summary: `Summary for ${over.name}.`,
    source: over.type === 'user_authored' ? 'custom' : 'bundled',
    policy_class: 'guidance',
    status: 'active',
    version: '1.0.0',
    ...over,
  };
}

const CONTRACT = row({
  skill_id: 'c1',
  name: 'founder-escalation-protocol',
  type: 'system_contract',
  system_contract: true,
  visibility_category: 'read_only',
  version: 'locked',
  assigned_agent_count: 5,
  effective_agent_count: 5,
});
const MANAGED = row({
  skill_id: 'm1',
  name: 'kb-curation',
  type: 'managed',
  assigned_agent_count: 4,
  effective_agent_count: 3,
  has_assigned_not_yet_effective: true,
});
const CUSTOM_DRAFT = row({
  skill_id: 'u1',
  name: 'vendor-comms-style',
  type: 'user_authored',
  validation_state: 'failed_validation',
});
const CUSTOM_NEW = row({
  skill_id: 'u2',
  name: 'refund-decision-guide',
  type: 'user_authored',
  validation_state: 'in_catalog',
});

const ALL = [CONTRACT, MANAGED, CUSTOM_DRAFT, CUSTOM_NEW];

const B2_API = `/api/v1/orgs/${SLUG}/custom-skills/catalog`;
const B2_CUSTOM = {
  id: 'custom:agent/first',
  slug: 'agent-first-guidance',
  name: 'agent-first-guidance',
  description: 'B2 guidance created by an agent.',
  current_version_id: 1,
  retired_at: null,
  validation_state: 'validated',
  hidden_reason: 'no_eligibility_policy',
};

function mount(
  rows: Row[] = ALL,
  customSkills = [B2_CUSTOM],
  customCatalogResponse?: (request: Request) => Response | Promise<Response>,
) {
  const requests = { legacyFilters: [] as Array<string | null>, b2Catalog: 0 };
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/skills/catalog`, ({ request }) => {
      const filter = new URL(request.url).searchParams.get('filter');
      requests.legacyFilters.push(filter);
      const bucket = (r: Row) =>
        r.type === 'user_authored' ? 'Custom' : 'Bundled';
      const items = filter ? rows.filter((r) => bucket(r) === filter) : rows;
      return HttpResponse.json({ items });
    }),
    http.get(B2_API, ({ request }) => {
      requests.b2Catalog += 1;
      return customCatalogResponse?.(request) ?? HttpResponse.json({ skills: customSkills });
    }),
  );
  return { requests, ...renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills` }) };
}

describe('SkillsPage — Catalog (THR-092 Slice 1)', () => {
  test('renders catalog rows with names and summaries', async () => {
    mount();
    expect(await screen.findByText('kb-curation')).toBeInTheDocument();
    expect(screen.getByText('founder-escalation-protocol')).toBeInTheDocument();
    expect(screen.getByText('Summary for kb-curation.')).toBeInTheDocument();
  });

  test('shows the guidance-visibility-only global warning', async () => {
    mount();
    await screen.findByText('kb-curation');
    expect(screen.getByText(/Guidance visibility only/i)).toBeInTheDocument();
    expect(
      screen.getByText(/never grant tools, commands, or permissions/i),
    ).toBeInTheDocument();
  });

  test('renders validation state in product language', async () => {
    mount();
    await screen.findByText('kb-curation');
    // failed_validation → "Needs attention" label on the custom draft.
    expect(screen.getAllByText('Needs attention').length).toBeGreaterThan(0);
    // in_catalog → "In catalog"
    expect(screen.getByText('In catalog')).toBeInTheDocument();
    // validated managed skill → "Validated"
    expect(screen.getAllByText('Validated').length).toBeGreaterThan(0);
  });

  test('does not expose a Proposals navigation link', async () => {
    mount();
    await screen.findByText('kb-curation');
    expect(screen.queryByRole('link', { name: 'Proposals' })).not.toBeInTheDocument();
  });

  test('retired proposals URL shows unavailable state on the catalog 404 without legacy controls', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/skills/catalog/proposals`,
        () => HttpResponse.json({ detail: { code: 'not_found', skill_id: 'proposals' } }, { status: 404 }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });

    expect(await screen.findByText('Could not load this skill')).toBeInTheDocument();
    expect(
      screen.getByText('This skill is unavailable right now, or the link is out of date.'),
    ).toBeInTheDocument();
    expect(document.querySelector('main')?.textContent).not.toMatch(
      /proposal|queue|approve|reject|claim/i,
    );
  });

  test('exposes an "Add custom skill" entry point to the create route', async () => {
    mount();
    await screen.findByText('kb-curation');
    const add = screen.getByRole('link', { name: /Add custom skill/i });
    expect(add).toHaveAttribute('href', `/orgs/${SLUG}/skills/custom/new`);
  });

  test('read-only system contract shows no toggle/edit control', async () => {
    mount();
    await screen.findByText('founder-escalation-protocol');
    const card = screen
      .getByText('founder-escalation-protocol')
      .closest('article') as HTMLElement;
    expect(within(card).queryByRole('switch')).toBeNull();
    expect(within(card).queryByRole('checkbox')).toBeNull();
    expect(within(card).queryByRole('button')).toBeNull();
    expect(
      within(card).getByText(/cannot be edited or unassigned/i),
    ).toBeInTheDocument();
  });

  test('read-only system contract still renders its validation badge', async () => {
    mount();
    await screen.findByText('founder-escalation-protocol');
    const card = screen
      .getByText('founder-escalation-protocol')
      .closest('article') as HTMLElement;
    // The skill-level validation badge renders on EVERY catalog row — read-only
    // only suppresses interactive controls, not the validation_state label.
    expect(within(card).getByText('Validated')).toBeInTheDocument();
  });

  test('read-only lock label is wrapped in a positioned container (THR-092 whole-surface-scroll guard)', async () => {
    mount();
    await screen.findByText('founder-escalation-protocol');
    const card = screen
      .getByText('founder-escalation-protocol')
      .closest('article') as HTMLElement;
    // The "Read-only system contract" lock label is screen-reader-only, and the
    // `sr-only` utility makes it position:absolute. Its wrapping <div> MUST be a
    // positioning context (`relative`) — otherwise the span's containing block
    // resolves to the ICB, it escapes the catalog's overflow-y-auto scroller,
    // and the WHOLE surface window-scrolls (the founder-reported THR-092 bug).
    // Token-aware (classList.contains), never a word-boundary regex.
    const srLabel = within(card).getByText('Read-only system contract', {
      selector: 'span',
    });
    const wrapper = srLabel.parentElement as HTMLElement;
    expect(wrapper.classList.contains('relative')).toBe(true);
  });

  test('only Bundled and Custom are exposed as filter controls (no "All skills")', async () => {
    mount();
    await screen.findByText('founder-escalation-protocol');
    // Facets render in both the desktop rail and the mobile chips (jsdom
    // ignores `md:` visibility), so each label appears twice.
    expect(screen.getAllByRole('button', { name: 'Bundled' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Custom' })).toHaveLength(2);
    expect(
      screen.queryByRole('button', { name: /all skills/i }),
    ).toBeNull();
    expect(screen.queryByRole('button', { name: /^All$/ })).toBeNull();
  });

  test('takes-effect-next-session indicator for not-yet-effective assignments', async () => {
    mount();
    const card = (await screen.findByText('kb-curation')).closest(
      'article',
    ) as HTMLElement;
    expect(
      within(card).getByText('Takes effect next session'),
    ).toBeInTheDocument();
  });

  test('Custom facet uses the canonical B2 catalog, renders its default-hidden status, and keeps legacy rows out', async () => {
    const { requests } = mount();
    await screen.findByText('founder-escalation-protocol');
    expect(requests.b2Catalog).toBe(0);
    await userEvent.click(
      screen.getAllByRole('button', { name: 'Custom' })[0],
    );
    expect(await screen.findByText('agent-first-guidance')).toBeInTheDocument();
    expect(screen.getByText('Hidden — eligibility not configured')).toBeInTheDocument();
    expect(screen.queryByText('vendor-comms-style')).not.toBeInTheDocument();
    expect(screen.queryByText('founder-escalation-protocol')).not.toBeInTheDocument();
    expect(screen.queryByText('kb-curation')).not.toBeInTheDocument();
    expect(requests.b2Catalog).toBe(1);
    expect(requests.legacyFilters).not.toContain('Custom');
  });

  test('Custom facet links B2 rows to the encoded B2 editor route and never shows its legacy empty state', async () => {
    mount();
    await screen.findByText('kb-curation');
    await userEvent.click(screen.getAllByRole('button', { name: 'Custom' })[0]);
    const custom = await screen.findByRole('link', { name: 'View agent-first-guidance' });
    expect(custom).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/skills/custom/${encodeURIComponent(B2_CUSTOM.id)}`,
    );
    expect(screen.queryByText('No custom skills yet')).not.toBeInTheDocument();
  });

  test('Custom facet owns the B2 loading state', async () => {
    mount(ALL, [], () => new Promise<Response>(() => {}));
    await screen.findByText('kb-curation');
    await userEvent.click(screen.getAllByRole('button', { name: 'Custom' })[0]);
    expect(document.querySelectorAll('[aria-hidden="true"] .animate-pulse')).toHaveLength(3);
  });

  test('Custom facet owns the B2 empty state', async () => {
    mount(ALL, []);
    await screen.findByText('kb-curation');
    await userEvent.click(screen.getAllByRole('button', { name: 'Custom' })[0]);
    expect(await screen.findByText('No skills here yet')).toBeInTheDocument();
    expect(screen.getByText('No custom skills yet. Custom skills you add will appear here.')).toBeInTheDocument();
  });

  test('Custom facet owns B2 generic-error and founder-denied states', async () => {
    mount(ALL, [], () => new HttpResponse('unavailable', { status: 500 }));
    await screen.findByText('kb-curation');
    await userEvent.click(screen.getAllByRole('button', { name: 'Custom' })[0]);
    expect(await screen.findByText('Could not load custom skills')).toBeInTheDocument();
  });

  test('Custom facet keeps the founder-denied B2 state at its owning surface', async () => {
    mount(ALL, [], () => new HttpResponse('forbidden', { status: 403 }));
    await screen.findByText('kb-curation');
    await userEvent.click(screen.getAllByRole('button', { name: 'Custom' })[0]);
    expect(await screen.findByText('Founder access required')).toBeInTheDocument();
  });

  test('Custom facet switches to removed tombstones without changing the Bundled catalog', async () => {
    const removed = { ...B2_CUSTOM, state: 'permanently_removed', hidden_reason: 'purged', purged_at: '2026-08-30T01:02:03Z' };
    const { requests } = mount(ALL, [B2_CUSTOM], (request) => HttpResponse.json({
      skills: new URL(request.url).searchParams.get('view') === 'removed' ? [removed] : [B2_CUSTOM],
    }));
    await screen.findByText('kb-curation');
    await userEvent.click(screen.getAllByRole('button', { name: 'Custom' })[0]);
    expect(await screen.findByText('agent-first-guidance')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Add custom skill' })).toHaveAttribute(
      'href', `/orgs/${SLUG}/skills/custom/new`,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Removed' }));
    expect(await screen.findByText('Permanently removed')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Add custom skill' })).not.toBeInTheDocument();
    expect(screen.getByText('Reservation retained')).toBeInTheDocument();
    expect(requests.legacyFilters).not.toContain('Custom');
    expect(requests.legacyFilters).toEqual([null]);
  });

  test('root flex container carries bounded-height classes so scroll is inner-region only (THR-092)', async () => {
    mount();
    await screen.findByText('kb-curation');

    // The catalog page has a two-column flex layout. Without min-h-0 + overflow-hidden
    // on the root flex container, flex items keep default min-height:auto and push the
    // whole AppShell surface to scroll. The bounded-height pattern (AuditPage.tsx:141-201)
    // ensures h-full is respected and child overflow-y-auto regions engage.
    const root = document.querySelector('.mx-auto.flex.h-full.w-full.max-w-6xl');
    expect(root).not.toBeNull();
    expect(root!.className).toMatch(/\bmin-h-0\b/);
    expect(root!.className).toMatch(/\boverflow-hidden\b/);
  });

  test('main scroll column carries bounded-body classes min-h-0 flex-1 overflow-y-auto (THR-092)', async () => {
    mount();
    await screen.findByText('kb-curation');

    // The main content column is the direct child of the root flex container
    // that also carries min-w-0 (the aside only has overflow-y-auto). It MUST
    // carry min-h-0 + flex-1 (unconditional, not md:-scoped) so it becomes the
    // bounded inner scroll box in BOTH desktop flex-row and mobile flex-col
    // layouts — exactly matching AuditPage's bounded-body pattern.
    const root = document.querySelector('.mx-auto.flex.h-full.w-full.max-w-6xl');
    expect(root).not.toBeNull();
    const mainColumn = root!.querySelector(':scope > .min-w-0.overflow-y-auto');
    expect(mainColumn).not.toBeNull();
    expect(mainColumn!.className).toMatch(/\bmin-h-0\b/);
    expect(mainColumn!.classList.contains('flex-1')).toBe(true);
    expect(mainColumn!.classList.contains('md:flex-1')).toBe(false);
    expect(mainColumn!.className).toMatch(/\boverflow-y-auto\b/);
  });

  test('copy discipline: no "active"/approve/admit/materialize-now UI language', async () => {
    mount();
    await screen.findByText('kb-curation');
    // Scope to the routed content column: the guidance strip legitimately
    // NEGATES permission wording ("never grant ... permissions"), so a blanket
    // body scan would false-positive on it. What must never appear is
    // capability/approval UI language.
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/\bactive\b/i);
    expect(main).not.toMatch(/\bapprove\b/i);
    expect(main).not.toMatch(/\badmit\b/i);
    expect(main).not.toMatch(/\bpending\b/i);
    expect(main).not.toMatch(/materialize now/i);
  });
});
