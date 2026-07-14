import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

interface Assignment {
  agent: string;
  assigned: boolean;
  effective: boolean;
  state: string;
}

interface Detail {
  skill_id: string;
  name: string;
  type: 'managed' | 'system_contract' | 'user_authored';
  source: string;
  system_contract: boolean;
  visibility_category: string;
  policy_class: string;
  status: string;
  version: string;
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  summary: string;
  description: string;
  when_to_use: string;
  owner: string;
  validation?: { ok: boolean; errors: string[] };
  assignments?: Assignment[];
}

const CONTRACT: Detail = {
  skill_id: 'sk-contract',
  name: 'founder-escalation-protocol',
  type: 'system_contract',
  source: 'bundled · contracts/founder-escalation-protocol/SKILL.md',
  system_contract: true,
  visibility_category: 'read_only',
  policy_class: 'contract',
  status: 'enabled',
  version: 'locked',
  validation_state: 'validated',
  summary: 'Escalate merges to main and genuine ambiguity to the founder.',
  description: 'A system contract shown to every agent by context.',
  when_to_use: 'Before merging to main or acting under ambiguity.',
  owner: 'platform',
  validation: { ok: true, errors: [] },
};

const MANAGED: Detail = {
  skill_id: 'sk-kb',
  name: 'kb-curation',
  type: 'managed',
  source: 'bundled · skills/kb-curation/SKILL.md',
  system_contract: false,
  visibility_category: 'toggleable',
  policy_class: 'guidance',
  status: 'enabled',
  version: '2.1.0',
  validation_state: 'validated',
  summary: 'How to curate durable cross-agent knowledge.',
  description: 'Guidance for when to add and promote a KB entry.',
  when_to_use: 'When a task uncovers a durable cross-agent fact.',
  owner: 'platform',
  validation: { ok: true, errors: [] },
  assignments: [
    { agent: 'kb_curator', assigned: true, effective: true, state: 'effective' },
    {
      agent: 'support_agent',
      assigned: true,
      effective: false,
      state: 'assigned_not_yet_effective',
    },
    { agent: 'sales_agent', assigned: false, effective: false, state: 'effective' },
  ],
};

const CUSTOM: Detail = {
  skill_id: 'sk-tourism',
  name: 'tourism-partner-playbook',
  type: 'user_authored',
  source: 'custom · store/tourism-partner-playbook/SKILL.md',
  system_contract: false,
  visibility_category: 'toggleable',
  policy_class: 'guidance',
  status: 'enabled',
  version: '1.2.0',
  validation_state: 'validated',
  summary: 'House style for briefing partner venues.',
  description: 'Your house style for partner-venue briefings.',
  when_to_use: 'When drafting a brief to a partner venue.',
  owner: 'operator',
  validation: { ok: true, errors: [] },
  assignments: [
    { agent: 'partner_liaison', assigned: true, effective: true, state: 'effective' },
  ],
};

const CUSTOM_FAILED: Detail = {
  skill_id: 'sk-vendor',
  name: 'vendor-comms-style',
  type: 'user_authored',
  source: 'custom · store/vendor-comms-style/SKILL.md',
  system_contract: false,
  visibility_category: 'toggleable',
  policy_class: 'guidance',
  status: 'draft',
  version: '0.3.0',
  validation_state: 'failed_validation',
  summary: 'Tone and escalation rules for vendor emails.',
  description: 'Draft guidance — saved and editable while you fix it.',
  when_to_use: 'When writing to a vendor.',
  owner: 'operator',
  validation: {
    ok: false,
    errors: ['SKILL.md is missing a required version field.'],
  },
  assignments: [],
};

const BY_ID: Record<string, Detail> = {
  [CONTRACT.skill_id]: CONTRACT,
  [MANAGED.skill_id]: MANAGED,
  [CUSTOM.skill_id]: CUSTOM,
  [CUSTOM_FAILED.skill_id]: CUSTOM_FAILED,
};

function mount(skillId: string) {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(
      `/api/v1/orgs/${SLUG}/skills/catalog/:skillId`,
      ({ params }) => {
        const detail = BY_ID[params.skillId as string];
        return detail
          ? HttpResponse.json(detail)
          : HttpResponse.json({ detail: 'not found' }, { status: 404 });
      },
    ),
  );
  return renderWithProviders(<AppRoutes />, {
    route: `/orgs/${SLUG}/skills/${skillId}`,
  });
}

describe('SkillDetailPage — detail + provenance (THR-092 Slice 2)', () => {
  test('renders source and SKILL.md content', async () => {
    mount(CUSTOM.skill_id);
    expect(await screen.findByText('tourism-partner-playbook')).toBeInTheDocument();
    expect(
      screen.getByText('custom · store/tourism-partner-playbook/SKILL.md'),
    ).toBeInTheDocument();
    expect(screen.getByText('When to use')).toBeInTheDocument();
    expect(
      screen.getByText('Your house style for partner-venue briefings.'),
    ).toBeInTheDocument();
  });

  test('read-only system contract: lock, NO edit/re-validate control, applied-by-context', async () => {
    mount(CONTRACT.skill_id);
    await screen.findByText('founder-escalation-protocol');
    expect(screen.getByText(/^Read-only$/)).toBeInTheDocument();
    expect(
      screen.getByText(/cannot be edited or unassigned/i),
    ).toBeInTheDocument();
    // No edit / re-validate affordance for a read-only source.
    expect(
      screen.queryByRole('link', { name: /edit skill/i }),
    ).toBeNull();
    expect(
      screen.queryByRole('button', { name: /re-?validate/i }),
    ).toBeNull();
    expect(
      screen.getByText(/applied to agents by context/i),
    ).toBeInTheDocument();
  });

  test('custom skill shows an EDIT entry point targeting the Slice-4 route', async () => {
    mount(CUSTOM.skill_id);
    await screen.findByText('tourism-partner-playbook');
    const edit = screen.getByRole('link', { name: /edit skill/i });
    expect(edit).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/skills/${CUSTOM.skill_id}/edit`,
    );
  });

  test('failed validation shows a Needs attention label + plain-language issues; draft stays editable', async () => {
    mount(CUSTOM_FAILED.skill_id);
    await screen.findByText('vendor-comms-style');
    expect(screen.getAllByText('Needs attention').length).toBeGreaterThan(0);
    expect(
      screen.getByText('SKILL.md is missing a required version field.'),
    ).toBeInTheDocument();
    // A failed custom draft is still editable (fix & re-validate in Slice 4).
    expect(
      screen.getByRole('link', { name: /edit skill/i }),
    ).toBeInTheDocument();
  });

  test('per-agent provenance: effective, takes-effect-next-session, and not-assigned', async () => {
    mount(MANAGED.skill_id);
    await screen.findByText('kb-curation');

    const effectiveRow = document.querySelector(
      '[data-agent="kb_curator"]',
    ) as HTMLElement;
    expect(within(effectiveRow).getByText('Effective')).toBeInTheDocument();

    const pendingRow = document.querySelector(
      '[data-agent="support_agent"]',
    ) as HTMLElement;
    expect(
      within(pendingRow).getByText('Takes effect next session'),
    ).toBeInTheDocument();
    expect(
      within(pendingRow).getByText(/takes effect at this agent/i),
    ).toBeInTheDocument();

    const unassignedRow = document.querySelector(
      '[data-agent="sales_agent"]',
    ) as HTMLElement;
    expect(within(unassignedRow).getByText('Not assigned')).toBeInTheDocument();
    expect(
      within(unassignedRow).getByText(/not shown to this agent as guidance/i),
    ).toBeInTheDocument();
  });

  test('managed bundled skill is read-only (no edit) yet still lists per-agent status', async () => {
    mount(MANAGED.skill_id);
    await screen.findByText('kb-curation');
    expect(screen.queryByRole('link', { name: /edit skill/i })).toBeNull();
    expect(screen.getByText(/managed by the platform/i)).toBeInTheDocument();
    expect(
      document.querySelector('[data-agent="kb_curator"]'),
    ).not.toBeNull();
  });

  test('copy discipline: no active/approve/admit/pending/materialize-now language', async () => {
    mount(MANAGED.skill_id);
    await screen.findByText('kb-curation');
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/\bactive\b/i);
    expect(main).not.toMatch(/\bapprove\b/i);
    expect(main).not.toMatch(/\badmit\b/i);
    expect(main).not.toMatch(/\bpending\b/i);
    expect(main).not.toMatch(/materialize now/i);
  });
});
