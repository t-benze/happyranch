import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import type { ValidationEvent } from '@/hooks/skills';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function ev(over: Partial<ValidationEvent> & Pick<ValidationEvent, 'id'>): ValidationEvent {
  return {
    skill_id: 'sk-refund',
    slug: 'refund-decision-guide',
    agent: 'support_agent',
    source: 'user_authored',
    severity: 'pass',
    ok: true,
    version: '1.0.0',
    findings: [],
    reason_codes: [],
    created_at: '2026-07-15T09:00:00Z',
    ...over,
  };
}

const PASS = ev({ id: 1 });
const FAIL = ev({
  id: 2,
  skill_id: 'sk-vendor',
  slug: 'vendor-comms-style',
  agent: 'vendor_desk',
  source: 'user_authored',
  severity: 'error',
  ok: false,
  version: '0.3.0',
  findings: ['SKILL.md is missing a required version field.'],
  reason_codes: ['missing_version'],
});
const BUNDLED = ev({
  id: 3,
  skill_id: 'sk-kb',
  slug: 'kb-curation',
  agent: 'research_lead',
  source: 'first_party',
  severity: 'pass',
});
const CONTEXT = ev({
  id: 4,
  skill_id: 'sk-contract',
  slug: 'founder-escalation-protocol',
  agent: null,
  source: 'runtime',
  severity: 'error',
  ok: false,
  version: 'locked',
  findings: ['A contract rule could not be checked for one or more agents.'],
  reason_codes: ['contract_predicate_error'],
});

const ALL = [PASS, FAIL, BUNDLED, CONTEXT];

function mount(events: ValidationEvent[] = ALL) {
  const requests: string[] = [];
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/skills/validation`, ({ request }) => {
      const url = new URL(request.url);
      requests.push(url.search);
      const p = url.searchParams;
      let out = [...events];
      const skill = p.get('skill');
      if (skill) out = out.filter((e) => e.skill_id === skill);
      const agent = p.get('agent');
      if (agent) out = out.filter((e) => e.agent === agent);
      const source = p.get('source');
      if (source) out = out.filter((e) => e.source === source);
      const severity = p.get('severity');
      if (severity) out = out.filter((e) => e.severity === severity);
      const since = p.get('since');
      if (since) {
        const floor = Date.parse(since);
        out = out.filter((e) => Date.parse(e.created_at) >= floor);
      }
      return HttpResponse.json({ events: out, label: 'Runtime Validation' });
    }),
  );
  const utils = renderWithProviders(<AppRoutes />, {
    route: `/orgs/${SLUG}/skills/validation`,
  });
  return { ...utils, requests };
}

describe('SkillValidationPage — Runtime Validation (THR-092 Slice 6)', () => {
  test('titles the surface from the endpoint label, never "Audit"', async () => {
    mount();
    expect(
      await screen.findByRole('heading', { name: 'Runtime Validation' }),
    ).toBeInTheDocument();
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/\baudit\b/i);
  });

  test('renders event rows across severities and sources in product language', async () => {
    mount();
    // Skill names are queried as row LINKS — the slugs also appear as skill-
    // filter <option> labels, so a plain text query would be ambiguous.
    expect(
      await screen.findByRole('link', { name: 'vendor-comms-style' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'kb-curation' })).toBeInTheDocument();
    // severity → product badges
    expect(screen.getAllByText('Needs attention').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Passed').length).toBeGreaterThan(0);
    // source → Bundled/Custom/Runtime labels
    expect(screen.getAllByText('Bundled').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Runtime').length).toBeGreaterThan(0);
  });

  test('null-agent event renders the applied-by-context label, never blank', async () => {
    mount();
    await screen.findByRole('link', { name: 'founder-escalation-protocol' });
    expect(
      screen.getByText('Applied by context — all agents'),
    ).toBeInTheDocument();
  });

  test('reason codes render as plain language, never raw enum jargon', async () => {
    mount();
    await screen.findByRole('link', { name: 'vendor-comms-style' });
    expect(
      screen.getByText('The skill guide is missing a version.'),
    ).toBeInTheDocument();
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/missing_version|contract_predicate_error/);
  });

  test('skill name links to the Slice-2 detail route', async () => {
    mount();
    const link = await screen.findByRole('link', { name: 'vendor-comms-style' });
    expect(link).toHaveAttribute('href', `/orgs/${SLUG}/skills/sk-vendor`);
  });

  test('a severity filter drives the endpoint query param and narrows the list', async () => {
    const { requests } = mount();
    await screen.findByRole('link', { name: 'kb-curation' });
    // Desktop + mobile-drawer both render a "Result" select; either drives the
    // same filter state (jsdom ignores the `md:` visibility utilities).
    const severitySelect = screen.getAllByLabelText('Result')[0];
    await userEvent.selectOptions(severitySelect, 'error');
    await waitFor(() =>
      expect(requests.some((s) => s.includes('severity=error'))).toBe(true),
    );
    // The passing bundled ROW drops out (its slug lingers only as a filter
    // <option>, derived from the unfiltered options query); the failing rows
    // remain.
    await waitFor(() =>
      expect(
        screen.queryByRole('link', { name: 'kb-curation' }),
      ).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole('link', { name: 'vendor-comms-style' }),
    ).toBeInTheDocument();
  });

  test('shows the guidance empty state when there are no events', async () => {
    mount([]);
    expect(
      await screen.findByText('No runtime validation events yet'),
    ).toBeInTheDocument();
  });

  test('copy discipline: no forbidden lifecycle/permission tokens, no user-facing "active"', async () => {
    mount();
    await screen.findByRole('link', { name: 'vendor-comms-style' });
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/materializ|admit|permission|approve|grant|\bpending\b/i);
    expect(main).not.toMatch(/\bactive\b/i);
  });
});
