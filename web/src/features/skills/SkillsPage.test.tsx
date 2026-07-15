import { screen, waitFor, within } from '@testing-library/react';
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

function mount(rows: Row[] = ALL) {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/skills/catalog`, ({ request }) => {
      const filter = new URL(request.url).searchParams.get('filter');
      const bucket = (r: Row) =>
        r.type === 'user_authored' ? 'Custom' : 'Bundled';
      const items = filter ? rows.filter((r) => bucket(r) === filter) : rows;
      return HttpResponse.json({ items });
    }),
  );
  return renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills` });
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

  test('exposes an "Add custom skill" entry point to the create route', async () => {
    mount();
    await screen.findByText('kb-curation');
    const add = screen.getByRole('link', { name: /Add custom skill/i });
    expect(add).toHaveAttribute('href', `/orgs/${SLUG}/skills/new`);
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

  test('Custom filter maps to the ?filter= param and narrows the list', async () => {
    mount();
    await screen.findByText('founder-escalation-protocol');
    // Both the desktop rail facet and the mobile chip expose a "Custom"
    // button (jsdom ignores the `md:` visibility utilities); either toggles
    // the same filter state.
    await userEvent.click(
      screen.getAllByRole('button', { name: 'Custom' })[0],
    );
    await waitFor(() =>
      expect(
        screen.queryByText('founder-escalation-protocol'),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText('vendor-comms-style')).toBeInTheDocument();
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
