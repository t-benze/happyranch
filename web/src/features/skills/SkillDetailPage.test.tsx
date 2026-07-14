import { fireEvent, screen, waitFor, within } from '@testing-library/react';
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

// ── Slice-5 per-agent assignment status (drives the custom assignment table).
interface StatusAssignment {
  agent: string;
  assigned: boolean;
  effective: boolean;
  materialized_version: string | null;
  state: 'effective' | 'assigned_not_yet_effective';
}
interface StatusResponse {
  skill_id: string;
  source: string;
  in_catalog: boolean;
  validated: boolean;
  current_version: string;
  assignments: StatusAssignment[];
  last_validation: { ok: boolean; version: string | null; at: string | null } | null;
}

// Custom skill status spans the full vocabulary: an effective agent, an
// assigned-not-yet-effective agent, and a not-assigned agent (candidate).
const CUSTOM_STATUS: StatusResponse = {
  skill_id: CUSTOM.skill_id,
  source: 'user_authored',
  in_catalog: true,
  validated: true,
  current_version: '1.2.0',
  assignments: [
    {
      agent: 'partner_liaison',
      assigned: true,
      effective: true,
      materialized_version: '1.2.0',
      state: 'effective',
    },
    {
      agent: 'support_agent',
      assigned: true,
      effective: false,
      materialized_version: '1.1.0',
      state: 'assigned_not_yet_effective',
    },
    {
      agent: 'ops_agent',
      assigned: false,
      effective: false,
      materialized_version: null,
      state: 'assigned_not_yet_effective',
    },
  ],
  last_validation: { ok: true, version: '1.2.0', at: '2026-07-14T10:00:00Z' },
};

const STATUS_BY_ID: Record<string, StatusResponse> = {
  [CUSTOM.skill_id]: CUSTOM_STATUS,
};

function mount(skillId: string) {
  sessionStorage.setItem('happyranch.token', 'tok');
  // Stateful clone so a commit (assign POST) mutates the store and the status
  // refetch reflects the applied change (MEM-037 stateful-mock pattern).
  const statusStore: Record<string, StatusResponse> = JSON.parse(
    JSON.stringify(STATUS_BY_ID),
  );
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
    http.get(`/api/v1/orgs/${SLUG}/skills/:skillId/status`, ({ params }) => {
      const s = statusStore[params.skillId as string];
      return s
        ? HttpResponse.json(s)
        : HttpResponse.json({ detail: 'not found' }, { status: 404 });
    }),
    http.post(
      `/api/v1/orgs/${SLUG}/agents/:agentId/skills/:skillId/assign`,
      async ({ params, request }) => {
        const body = (await request.json()) as { action: 'allow' | 'remove' };
        const agentId = params.agentId as string;
        const sid = params.skillId as string;
        const assigning = body.action === 'allow';
        const s = statusStore[sid];
        if (s) {
          const row = s.assignments.find((x) => x.agent === agentId);
          if (row) {
            row.assigned = assigning;
            row.effective = false;
            row.state = 'assigned_not_yet_effective';
          }
        }
        return HttpResponse.json({
          agent_id: agentId,
          skill_id: sid,
          state: assigning ? 'assigned' : 'unassigned',
          effective_hint: assigning ? 'assigned_not_yet_effective' : null,
          materializes_on: assigning ? 'next_session' : null,
        });
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

describe('SkillDetailPage — custom assignment + config-review (THR-092 Slice 5)', () => {
  test('custom skill shows the interactive per-agent assignment table with all three states + toggles', async () => {
    mount(CUSTOM.skill_id);
    // Wait for the assignment table (status-driven) to render.
    const liaison = (await screen.findByText('partner_liaison')).closest(
      '[data-agent]',
    ) as HTMLElement;
    expect(liaison.getAttribute('data-status')).toBe('effective');
    expect(within(liaison).getByText('Effective')).toBeInTheDocument();

    const support = document.querySelector(
      '[data-agent="support_agent"]',
    ) as HTMLElement;
    expect(support.getAttribute('data-status')).toBe('not_yet_effective');
    expect(
      within(support).getByText('Takes effect next session'),
    ).toBeInTheDocument();

    const ops = document.querySelector(
      '[data-agent="ops_agent"]',
    ) as HTMLElement;
    expect(ops.getAttribute('data-status')).toBe('not_assigned');
    expect(within(ops).getByText('Not assigned')).toBeInTheDocument();

    // Each row carries a guidance-visibility toggle (Assign / Unassign), never
    // the api verb or permission wording.
    expect(
      within(ops).getByRole('button', { name: /^assign ops_agent$/i }),
    ).toBeInTheDocument();
    expect(
      within(liaison).getByRole('button', { name: /^unassign partner_liaison$/i }),
    ).toBeInTheDocument();
  });

  test('toggling an agent queues a change, previews optimistically, and reveals Review & apply', async () => {
    mount(CUSTOM.skill_id);
    const ops = (await screen.findByText('ops_agent')).closest(
      '[data-agent]',
    ) as HTMLElement;

    fireEvent.click(
      within(ops).getByRole('button', { name: /^assign ops_agent$/i }),
    );

    // Optimistic preview: newly-assigned → "Takes effect next session".
    expect(ops.getAttribute('data-status')).toBe('not_yet_effective');
    expect(ops.getAttribute('data-changed')).toBe('true');
    expect(within(ops).getByText(/will change/i)).toBeInTheDocument();
    // The row's toggle now offers the reverse action.
    expect(
      within(ops).getByRole('button', { name: /^unassign ops_agent$/i }),
    ).toBeInTheDocument();

    // The config-review affordance appears.
    expect(screen.getByText(/1 change to review/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /review & apply/i }),
    ).toBeInTheDocument();
  });

  test('config-review commit: review-before-commit summary, guidance-visibility note, applies the queued change', async () => {
    mount(CUSTOM.skill_id);
    const ops = (await screen.findByText('ops_agent')).closest(
      '[data-agent]',
    ) as HTMLElement;
    fireEvent.click(
      within(ops).getByRole('button', { name: /^assign ops_agent$/i }),
    );

    // Open the config-review summary.
    fireEvent.click(screen.getByRole('button', { name: /review & apply/i }));
    const review = screen.getByTestId('config-review');
    expect(
      within(review).getByText(/ops_agent will be shown this skill as guidance/i),
    ).toBeInTheDocument();
    // Guidance-visibility-only note at the commit action.
    expect(
      within(review).getByText(/do not change available tools or commands/i),
    ).toBeInTheDocument();

    // Commit.
    fireEvent.click(
      within(review).getByRole('button', { name: /^apply 1 change$/i }),
    );

    // Success confirmation + the committed table reflects the applied change
    // (stateful mock: ops_agent is now assigned-not-yet-effective).
    expect(
      await screen.findByText(/changes applied — they take effect/i),
    ).toBeInTheDocument();
    // The committed table reflects the applied change once the status refetch
    // (invalidated on commit) resolves.
    await waitFor(() => {
      const opsAfter = document.querySelector(
        '[data-agent="ops_agent"]',
      ) as HTMLElement;
      expect(opsAfter.getAttribute('data-status')).toBe('not_yet_effective');
      expect(opsAfter.getAttribute('data-changed')).toBe('false');
    });
    // The review summary is gone once applied.
    expect(screen.queryByTestId('config-review')).toBeNull();
  });

  test('custom-only gating: a read-only bundled skill shows NO assignment controls', async () => {
    mount(MANAGED.skill_id);
    await screen.findByText('kb-curation');
    // No per-agent toggle and no config-review affordance on a read-only skill.
    expect(
      screen.queryByRole('button', { name: /^(assign|unassign) /i }),
    ).toBeNull();
    expect(
      screen.queryByRole('button', { name: /review & apply/i }),
    ).toBeNull();
    // The Slice-2 read-only provenance list still renders (not regressed).
    expect(
      document.querySelector('[data-agent="kb_curator"]'),
    ).not.toBeNull();
  });

  test('copy discipline: the routed assignment + config-review surface has no forbidden token family and never renders the api verb', async () => {
    mount(CUSTOM.skill_id);
    const ops = (await screen.findByText('ops_agent')).closest(
      '[data-agent]',
    ) as HTMLElement;
    // Also toggle an existing agent OFF so an "Unassign" change is in the review.
    fireEvent.click(
      within(ops).getByRole('button', { name: /^assign ops_agent$/i }),
    );
    const support = document.querySelector(
      '[data-agent="support_agent"]',
    ) as HTMLElement;
    fireEvent.click(
      within(support).getByRole('button', { name: /^unassign support_agent$/i }),
    );
    fireEvent.click(screen.getByRole('button', { name: /review & apply/i }));
    screen.getByTestId('config-review');

    const main = document.querySelector('main')?.textContent ?? '';
    // The full forbidden lifecycle / permission / approval family…
    expect(main).not.toMatch(/materializ|admit|permission|approve|grant|\bpending\b/i);
    // …and user-facing "active" is rejected separately.
    expect(main).not.toMatch(/\bactive\b/i);
    // The api request verb ('allow'/'remove') is REQUEST-ONLY — never rendered.
    expect(main).not.toMatch(/\ballow\b/i);
    expect(main).not.toMatch(/\bremove\b/i);
  });
});
