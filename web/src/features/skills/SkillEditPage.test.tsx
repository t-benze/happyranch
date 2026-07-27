import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

// An editable custom skill with two effective agents + one already-not-yet-
// effective agent — exercises the edited-effective transform on a version bump.
const CUSTOM_ID = 'sk-tourism-partner-playbook';
const CUSTOM_DETAIL = {
  skill_id: CUSTOM_ID,
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
  description: 'The house style for partner-venue briefings.',
  when_to_use: 'When drafting a partner brief.',
  owner: 'operator',
  validation: { ok: true, errors: [] },
  assignments: [
    { agent: 'partner_liaison', assigned: true, effective: true, state: 'effective' },
    { agent: 'itinerary_planner', assigned: true, effective: true, state: 'effective' },
    {
      agent: 'support_agent',
      assigned: true,
      effective: false,
      state: 'assigned_not_yet_effective',
    },
  ],
};

// A read-only bundled skill — reaching /edit directly must NOT render the form.
const BUNDLED_ID = 'sk-kb-curation';
const BUNDLED_DETAIL = {
  ...CUSTOM_DETAIL,
  skill_id: BUNDLED_ID,
  name: 'kb-curation',
  type: 'managed',
  source: 'bundled · skills/kb-curation/SKILL.md',
  assignments: [],
};

// Lifecycle-native response shapes.
interface ProposalResponse {
  skill_id: string;
  version_id: number;
  version: string;
  status: string;
  content_hash: string;
  content_artifact_key: string | null;
  proposal_task_id: string | null;
}

const PASS_PROPOSAL: ProposalResponse = {
  skill_id: CUSTOM_ID,
  version_id: 99,
  version: '1.3.0',
  status: 'proposed',
  content_hash: 'abc123',
  content_artifact_key: null,
  proposal_task_id: null,
};

const LIFECYCLE_STATUS_VALIDATED = {
  skill_id: CUSTOM_ID,
  slug: 'tourism-partner-playbook',
  current_status: 'proposed',
  current_version: '1.3.0',
  current_version_id: 99,
  published_version: null,
  assignments: [],
  events: [],
  proposal_task_id: null,
  proposer_agent: null,
};

function mount(
  detail: typeof CUSTOM_DETAIL,
  opts: {
    proposalResponse?: ProposalResponse;
    proposalStatus?: number;
    skillId?: string;
    lifecycleStatus?: Record<string, unknown>;
  } = {},
) {
  const skillId = opts.skillId ?? CUSTOM_ID;
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/skills/catalog/:id`, () =>
      HttpResponse.json(detail),
    ),
    // Lifecycle proposal endpoint (replaces legacy PATCH /skills/:id).
    http.post(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals`, () =>
      HttpResponse.json(
        opts.proposalResponse ?? PASS_PROPOSAL,
        { status: opts.proposalStatus ?? 201 },
      ),
    ),
    // Lifecycle status read (used by Re-validate).
    http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/:skillId`, () =>
      HttpResponse.json(
        opts.lifecycleStatus ?? LIFECYCLE_STATUS_VALIDATED,
        { status: 200 },
      ),
    ),
  );
  return renderWithProviders(<AppRoutes />, {
    route: `/orgs/${SLUG}/skills/${skillId}/edit`,
  });
}

describe('SkillEditPage — edit + re-validate a custom skill (THR-092 Slice 4)', () => {
  test('prefills name / summary / version from the detail; no policy_class control; SKILL.md is blank (keeps current)', async () => {
    mount(CUSTOM_DETAIL);
    expect(
      await screen.findByRole('heading', { name: /Edit a custom skill/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Name/i)).toHaveValue('tourism-partner-playbook');
    expect(screen.getByLabelText(/Version/i)).toHaveValue('1.2.0');
    expect(screen.getByLabelText(/Summary/i)).toHaveValue(
      'House style for briefing partner venues.',
    );
    // Blank SKILL.md keeps the current guidance (daemon preserves it on omit).
    expect(screen.getByLabelText(/SKILL\.md/i)).toHaveValue('');
    // A custom skill can NEVER mint / alter a system_contract.
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/policy.?class/i);
    expect(main).not.toMatch(/system.?contract/i);
  });

  test('custom-only gating: a read-only bundled skill reached at /edit shows no form', async () => {
    mount(BUNDLED_DETAIL, { skillId: BUNDLED_ID });
    expect(
      await screen.findByText(/This skill is read-only/i),
    ).toBeInTheDocument();
    // No editable form, no submit.
    expect(screen.queryByLabelText(/SKILL\.md/i)).toBeNull();
    expect(
      screen.queryByRole('button', { name: /Save & re-validate/i }),
    ).toBeNull();
    // Still offers a way back to the detail surface.
    expect(screen.getByRole('link', { name: /View skill/i })).toBeInTheDocument();
  });

  test('required-field guard blocks submit when the name is cleared', async () => {
    mount(CUSTOM_DETAIL);
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.clear(screen.getByLabelText(/^Name/i));
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    expect(await screen.findByText('Add a name.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Validation result')).toBeNull();
  });

  test('PROPOSED + version bump: submits proposal, shows proposed state, confirmation, and View skill link', async () => {
    mount(CUSTOM_DETAIL);
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.clear(screen.getByLabelText(/Version/i));
    await userEvent.type(screen.getByLabelText(/Version/i), '1.3.0');
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));

    const result = await screen.findByLabelText('Validation result');
    expect(result).toHaveAttribute('data-result', 'proposed');
    expect(within(result).getByText('Proposed')).toBeInTheDocument();
    expect(within(result).getByText(/awaiting review/i)).toBeInTheDocument();
    // Proposed state adds a View skill link and no Re-validate.
    expect(within(result).getByRole('link', { name: /View skill/i })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/skills/${CUSTOM_ID}`,
    );
    expect(within(result).queryByRole('button', { name: /Re-validate/i })).toBeNull();
  });

  test('FAILURE (proposal error): shows submit-error message, no validation result section', async () => {
    // Lifecycle-native: a failed edit proposal (e.g., 409 slug collision)
    // is caught as a mutation error — not a "validation result" section.
    mount(CUSTOM_DETAIL, { proposalStatus: 409 });
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.type(screen.getByLabelText(/SKILL\.md/i), '# fail this validation');
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));

    expect(
      await screen.findByText(/Could not save the changes/i),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Validation result')).toBeNull();
  });

  test('SUCCESS path does not offer Re-validate (lifecycle-native: proposals are accepted, not iteratively validated)', async () => {
    mount(CUSTOM_DETAIL);
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    const result = await screen.findByLabelText('Validation result');
    // Success state never offers Re-validate (search within the result section
    // so the "Save & re-validate" submit button outside isn't matched).
    expect(within(result).queryByRole('button', { name: /Re-validate/i })).toBeNull();
  });

  test('copy discipline: the routed edit page uses no capability / approval / lifecycle language', async () => {
    mount(CUSTOM_DETAIL);
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.type(screen.getByLabelText(/SKILL\.md/i), '# fail');
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    await screen.findByLabelText('Validation result');
    const forbidden = /materializ|admit|permission|approve|grant|\bpending\b/i;
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(forbidden);
    expect(main).not.toMatch(/\bactive\b/i);
  });

  test('copy discipline: the proposed edit state also stays clean', async () => {
    mount(CUSTOM_DETAIL);
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    await screen.findByLabelText('Validation result');
    const forbidden = /materializ|admit|permission|approve|grant|\bpending\b/i;
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(forbidden);
    expect(main).not.toMatch(/\bactive\b/i);
  });
});
